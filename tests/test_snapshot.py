"""Snapshot storage: reruns are safe, and a rejected day writes nothing."""

import pytest

from src import snapshot
from src.ats import FetchError

SEED = "company,ats,slug,headcount,hq,notes\nAcme,ashby,acme,100,,\nBeta,greenhouse,beta,100,,\n"


def roles(company, ids, day):
    return [{
        "company": company, "ats": "ashby", "role_id": str(i), "title": "Engineer",
        "department": None, "team": None, "location": None, "is_remote": False,
        "url": None, "published_at": day, "captured_at": day,
    } for i in ids]


def day_counts(con):
    return dict(con.execute(
        "SELECT strftime(captured_at, '%Y-%m-%d'), COUNT(*) FROM role_snapshots GROUP BY 1"
    ).fetchall())


def test_rerunning_a_day_replaces_it(tmp_path):
    con = snapshot.get_connection(tmp_path / "t.duckdb")
    snapshot.write_snapshot(con, roles("Acme", [1, 2, 3], "2026-10-16"), "2026-10-16")
    snapshot.write_snapshot(con, roles("Acme", [1, 2, 3, 4], "2026-10-17"), "2026-10-17")
    snapshot.write_snapshot(con, roles("Acme", [1, 2], "2026-10-17"), "2026-10-17")  # rerun
    assert day_counts(con) == {"2026-10-16": 3, "2026-10-17": 2}


def test_previous_counts_uses_latest_earlier_day(tmp_path):
    con = snapshot.get_connection(tmp_path / "t.duckdb")
    snapshot.write_snapshot(con, roles("Acme", [1, 2, 3], "2026-10-15"), "2026-10-15")
    snapshot.write_snapshot(con, roles("Acme", [1, 2], "2026-10-16"), "2026-10-16")
    assert snapshot.previous_counts(con, "2026-10-17") == ("2026-10-16", {"Acme": 2})
    assert snapshot.previous_counts(con, "2026-10-15") == (None, {})


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A seed with two companies and one day of committed history."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    seed = tmp_path / "seed.csv"
    seed.write_text(SEED)
    snaps = tmp_path / "snapshots"
    con = snapshot.get_connection(tmp_path / "history.duckdb")
    snapshot.write_snapshot(con, roles("Acme", range(10), "2026-10-16")
                            + roles("Beta", range(100, 110), "2026-10-16"), "2026-10-16")
    snapshot.write_day_parquet(con, "2026-10-16", snaps)
    con.close()
    return tmp_path, seed, snaps


def run(tmp_path, seed, snaps):
    return snapshot.run_daily_snapshot(db_path=tmp_path / "run.duckdb", captured_at="2026-10-17",
                                       delay=0, snapshot_dir=snaps, seed_path=seed,
                                       second_pass_delay=0)


def test_failed_company_blocks_the_whole_day(project, monkeypatch):
    tmp_path, seed, snaps = project

    def fake_fetch(company, ats, slug, captured_at=None):
        if company == "Beta":
            raise FetchError("HTTP 503 after 4 attempts")
        return roles(company, range(10), captured_at)

    monkeypatch.setattr(snapshot, "fetch_company", fake_fetch)
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        run(tmp_path, seed, snaps)
    assert any("Beta" in e for e in exc.value.errors)
    assert not (snaps / "2026-10-17.parquet").exists()


def test_company_that_recovers_on_the_second_pass_is_saved(project, monkeypatch):
    """2026-10-28: Anduril timed out on every retry, then answered fine minutes later."""
    tmp_path, seed, snaps = project
    calls = []

    def flaky(company, ats, slug, captured_at=None):
        calls.append(company)
        if company == "Beta" and calls.count("Beta") == 1:
            raise FetchError("ReadTimeout after 4 attempts")
        return roles(company, range(10), captured_at)

    monkeypatch.setattr(snapshot, "fetch_company", flaky)
    stats = run(tmp_path, seed, snaps)
    assert stats["roles"] == 20
    assert calls == ["Acme", "Beta", "Beta"]  # only the failed company is retried
    assert (snaps / "2026-10-17.parquet").exists()


def test_company_reporting_zero_blocks_the_whole_day(project, monkeypatch):
    tmp_path, seed, snaps = project
    monkeypatch.setattr(snapshot, "fetch_company", lambda company, ats, slug, captured_at=None:
                        [] if company == "Beta" else roles(company, range(10), captured_at))
    with pytest.raises(snapshot.SnapshotRejected):
        run(tmp_path, seed, snaps)
    assert not (snaps / "2026-10-17.parquet").exists()


def test_healthy_day_is_saved(project, monkeypatch):
    tmp_path, seed, snaps = project
    monkeypatch.setattr(snapshot, "fetch_company", lambda company, ats, slug, captured_at=None:
                        roles(company, range(11), captured_at))
    stats = run(tmp_path, seed, snaps)
    assert stats["roles"] == 22
    assert (snaps / "2026-10-17.parquet").exists()
