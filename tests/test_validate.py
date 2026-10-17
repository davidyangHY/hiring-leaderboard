"""The validation gate: what blocks a snapshot and what doesn't."""

from src.validate import MAX_TOTAL_DROP, render_report, validate_snapshot


def test_normal_day_passes():
    res = validate_snapshot({"A": 102, "B": 49}, {}, {"A": 100, "B": 50})
    assert res.ok and not res.warnings


def test_any_fetch_failure_blocks():
    res = validate_snapshot({"A": 100}, {"B": "HTTP 503"}, {"A": 100, "B": 50})
    assert not res.ok
    assert any("B" in e and "HTTP 503" in e for e in res.errors)


def test_replay_spacex_dropout_is_caught():
    """Sep 28: SpaceX reported 0 roles and the run still went green.

    The total-volume check alone would NOT have caught it (~17% drop), which is
    why the per-company zero check exists.
    """
    prev = {"SpaceX": 2595, "Anduril": 2372, "Stripe": 500, "Everyone else": 10000}
    today = {**prev, "SpaceX": 0}
    total_drop = 2595 / sum(prev.values())
    assert total_drop < MAX_TOTAL_DROP

    res = validate_snapshot(today, {}, prev)
    assert not res.ok
    assert any(e.startswith("SpaceX: 0 roles today but 2595") for e in res.errors)


def test_tiny_company_may_legitimately_hit_zero():
    assert validate_snapshot({"Tiny": 0, "A": 100}, {}, {"Tiny": 3, "A": 100}).ok


def test_large_total_drop_blocks():
    res = validate_snapshot({"A": 300, "B": 300}, {}, {"A": 500, "B": 500})
    assert not res.ok
    assert any("total roles fell" in e for e in res.errors)


def test_removing_a_company_from_the_seed_is_not_a_drop():
    assert validate_snapshot({"A": 100}, {}, {"A": 100, "Removed": 900}).ok


def test_first_ever_snapshot_passes():
    res = validate_snapshot({"A": 100, "B": 0}, {}, None)
    assert res.ok
    assert {status for *_, status in res.rows} == {"new"}


def test_missing_posted_dates_block():
    res = validate_snapshot({"A": 100}, {}, {"A": 100}, null_date_rate=0.20)
    assert not res.ok


def test_big_partial_drop_is_a_warning_not_an_error():
    res = validate_snapshot({"A": 40, "B": 1000}, {}, {"A": 100, "B": 1000})
    assert res.ok
    assert res.warnings and "A" in res.warnings[0]


def test_report_shows_outcome_and_counts():
    blocked = render_report(validate_snapshot({"A": 0}, {}, {"A": 50}), "2026-10-17", "2026-10-16")
    assert "BLOCKED" in blocked and "| A | 50 | 0 | ZERO |" in blocked
    passed = render_report(validate_snapshot({"A": 50}, {}, None), "2026-10-17", None)
    assert "PASSED" in passed and "first snapshot" in passed
