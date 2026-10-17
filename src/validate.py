"""Validation gate for a day's snapshot.

Runs after every company has been fetched and *before* anything is written. If
it fails, the run fails: no Parquet is saved, nothing is committed, the site
keeps yesterday's data, and the failure alert fires.

The checks are shaped by real incidents. In the first 54 days of history, six
company-days silently dropped to zero roles (SpaceX twice, Anduril twice, Airbnb,
Reddit) while every run reported success. A total-volume check alone would have
missed all of them: losing SpaceX is only ~16% of the total, so the per-company
"had roles yesterday, has none today" check is the one that matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_TOTAL_DROP = 0.30          # fail if total roles fall more than this vs. the previous snapshot
MIN_PREV_FOR_ZERO_CHECK = 5    # a company that had at least this many roles can't drop to zero
MAX_NULL_DATE_RATE = 0.05      # fail if more than this share of roles have no posted date
WARN_COMPANY_DROP = 0.50       # warn (don't fail) if a company loses more than half its roles...
WARN_MIN_PREV = 20             # ...and had at least this many to begin with


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: list[tuple[str, int | None, int | None, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_snapshot(
    today_counts: dict[str, int],
    failures: dict[str, str],
    prev_counts: dict[str, int] | None = None,
    null_date_rate: float = 0.0,
) -> ValidationResult:
    """Check today's per-company role counts against the previous snapshot.

    ``today_counts`` has an entry for every company fetched successfully;
    ``failures`` maps each company that couldn't be fetched to its error.
    ``prev_counts`` is the previous snapshot's per-company counts (None or empty
    on the very first run, which skips the comparison checks).
    """
    res = ValidationResult()
    prev_counts = prev_counts or {}

    for company, err in sorted(failures.items()):
        res.errors.append(f"{company}: could not be fetched after retries ({err})")

    for company in sorted(set(today_counts) | set(failures)):
        today = today_counts.get(company)
        prev = prev_counts.get(company)
        if company in failures:
            status = "FAILED"
        elif prev is not None and prev >= MIN_PREV_FOR_ZERO_CHECK and today == 0:
            status = "ZERO"
            res.errors.append(
                f"{company}: 0 roles today but {prev} on the previous snapshot. If the board really "
                f"is empty or has moved, fix or remove it in seed/companies.csv."
            )
        elif prev is None:
            status = "new"
        elif prev >= WARN_MIN_PREV and today < prev * (1 - WARN_COMPANY_DROP):
            status = "dropped"
            res.warnings.append(f"{company}: {prev} → {today} roles (more than half gone)")
        else:
            status = "ok"
        res.rows.append((company, prev, today, status))

    # Only compare companies that are still tracked, so removing a company from
    # the seed list doesn't look like a drop.
    tracked = set(today_counts) | set(failures)
    prev_total = sum(n for c, n in prev_counts.items() if c in tracked)
    today_total = sum(today_counts.values())
    if prev_total and (prev_total - today_total) / prev_total > MAX_TOTAL_DROP:
        res.errors.append(
            f"total roles fell {prev_total:,} → {today_total:,} "
            f"({(prev_total - today_total) / prev_total:.0%}, limit {MAX_TOTAL_DROP:.0%})"
        )

    if null_date_rate > MAX_NULL_DATE_RATE:
        res.errors.append(
            f"{null_date_rate:.1%} of roles have no posted date (limit {MAX_NULL_DATE_RATE:.0%})"
        )
    return res


def render_report(res: ValidationResult, captured_at: str, prev_date: str | None) -> str:
    """Markdown summary for logs and the GitHub Actions run page."""
    today_total = sum(t for _, _, t, _ in res.rows if t is not None)
    prev_total = sum(p for _, p, _, _ in res.rows if p is not None)
    lines = [
        f"## Snapshot {captured_at}: {'PASSED' if res.ok else 'BLOCKED'}",
        "",
        f"{today_total:,} roles across {sum(1 for r in res.rows if r[2] is not None)} companies"
        + (f" (previous snapshot {prev_date}: {prev_total:,})" if prev_date else " (first snapshot)"),
    ]
    if res.errors:
        lines += ["", "**Errors (snapshot not saved):**"] + [f"- {e}" for e in res.errors]
    if res.warnings:
        lines += ["", "**Warnings:**"] + [f"- {w}" for w in res.warnings]
    lines += ["", "<details><summary>Per-company counts</summary>", "",
              "| Company | Previous | Today | Status |", "|---|---:|---:|---|"]
    for company, prev, today, status in res.rows:
        lines.append(f"| {company} | {'' if prev is None else prev} | "
                     f"{'' if today is None else today} | {status} |")
    lines += ["", "</details>"]
    return "\n".join(lines)
