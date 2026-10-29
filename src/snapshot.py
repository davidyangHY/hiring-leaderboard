"""Daily snapshot + change-data-capture into DuckDB.

Each run fetches every tracked company's currently-open roles and writes them as
one dated snapshot into ``role_snapshots``. Because we keep every day's snapshot
and the primary key is ``(captured_at, company, role_id)``, "new roles in a
window" is later derived by first-seen date:

    first_seen(role) = MIN(captured_at) over its (company, role_id)

so a role counts as *new* in the window that contains its first snapshot. Closed
roles simply stop appearing — their last snapshot marks when they went away.

Nothing is written until the day passes the validation gate (src/validate.py):
a company that can't be fetched, or one that suddenly reports zero roles, blocks
the whole snapshot rather than saving a partial one. Running twice in one day
replaces that day's rows, so reruns are safe. The ``companies`` dim table mirrors
the seed so downstream joins (e.g. roles per headcount) don't need the CSV.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

import duckdb

from src.ats import FetchError, fetch_company
from src.classify import classify_function
from src.seed import load_companies
from src.validate import render_report, validate_snapshot

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/hiring.duckdb")
# Committed, append-only history. The DuckDB file is a derived cache rebuilt
# from these each run, so snapshot history survives ephemeral CI runners.
SNAPSHOT_DIR = Path("data/snapshots")
# Companies that still fail after their own retries get one more try at the end
# of the run, after this pause. Short outages (e.g. a Greenhouse slowdown on one
# board) have usually cleared by then, so the day isn't blocked for a blip.
SECOND_PASS_DELAY_S = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company    VARCHAR PRIMARY KEY,
    ats        VARCHAR,
    slug       VARCHAR,
    headcount  BIGINT,
    hq         VARCHAR,
    notes      VARCHAR
);

CREATE TABLE IF NOT EXISTS role_snapshots (
    captured_at  DATE,
    company      VARCHAR,
    ats          VARCHAR,
    role_id      VARCHAR,
    title        VARCHAR,
    department   VARCHAR,
    function     VARCHAR,
    team         VARCHAR,
    location     VARCHAR,
    is_remote    BOOLEAN,
    url          VARCHAR,
    published_at DATE,
    PRIMARY KEY (captured_at, company, role_id)
);
"""


def get_connection(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(_SCHEMA)
    return con


def load_history(con: duckdb.DuckDBPyConnection, snapshot_dir: Path | None = None) -> int:
    """Load all committed daily Parquet snapshots into role_snapshots.

    This rebuilds full history on a fresh (e.g. CI) machine before today's fetch,
    so first-seen dates are computed against the complete record.
    """
    d = Path(snapshot_dir or SNAPSHOT_DIR)
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        return 0
    glob = str(d / "*.parquet").replace("\\", "/")
    con.execute(
        f"INSERT OR IGNORE INTO role_snapshots SELECT * FROM read_parquet('{glob}')"
    )
    return len(files)


def write_day_parquet(con: duckdb.DuckDBPyConnection, captured_at: str,
                      snapshot_dir: Path | None = None) -> Path:
    """Write one day's rows to data/snapshots/YYYY-MM-DD.parquet (the committed unit)."""
    d = Path(snapshot_dir or SNAPSHOT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{captured_at}.parquet"
    path = str(out).replace("\\", "/")
    con.execute(
        "COPY (SELECT * FROM role_snapshots WHERE captured_at = ? ORDER BY company, role_id) "
        f"TO '{path}' (FORMAT parquet)",
        [captured_at],
    )
    return out


def sync_companies(con: duckdb.DuckDBPyConnection, companies: list[dict]) -> None:
    """Upsert the company dimension from the seed rows."""
    rows = [
        (c["company"], c.get("ats"), c.get("slug"),
         c.get("headcount") or None, c.get("hq"), c.get("notes"))
        for c in companies
    ]
    con.executemany(
        """INSERT INTO companies (company, ats, slug, headcount, hq, notes)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (company) DO UPDATE SET
             ats=excluded.ats, slug=excluded.slug, headcount=excluded.headcount,
             hq=excluded.hq, notes=excluded.notes""",
        rows,
    )


def _to_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def previous_counts(con: duckdb.DuckDBPyConnection, captured_at: str) -> tuple[str | None, dict[str, int]]:
    """Per-company role counts from the most recent snapshot before ``captured_at``."""
    prev = con.execute(
        "SELECT MAX(captured_at) FROM role_snapshots WHERE captured_at < ?", [captured_at]
    ).fetchone()[0]
    if prev is None:
        return None, {}
    rows = con.execute(
        "SELECT company, COUNT(*) FROM role_snapshots WHERE captured_at = ? GROUP BY company", [prev]
    ).fetchall()
    return str(prev), {company: n for company, n in rows}


def write_snapshot(con: duckdb.DuckDBPyConnection, roles: list[dict], captured_at: str) -> int:
    """Store one day's normalized+classified roles, replacing any earlier run that day."""
    cap = _to_date(captured_at)
    rows = [
        (
            cap, r["company"], r.get("ats"), r["role_id"], r.get("title"),
            r.get("department"), classify_function(r.get("title"), r.get("department")),
            r.get("team"), r.get("location"), r.get("is_remote"),
            r.get("url"), _to_date(r.get("published_at")),
        )
        for r in roles
    ]
    con.execute("DELETE FROM role_snapshots WHERE captured_at = ?", [cap])
    if not rows:
        return 0
    con.executemany(
        """INSERT OR IGNORE INTO role_snapshots (
             captured_at, company, ats, role_id, title, department, function,
             team, location, is_remote, url, published_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return con.execute(
        "SELECT COUNT(*) FROM role_snapshots WHERE captured_at = ?", [cap]
    ).fetchone()[0]


class SnapshotRejected(Exception):
    """Today's snapshot failed validation, so nothing was written."""

    def __init__(self, report: str, errors: list[str]):
        super().__init__(f"{len(errors)} validation error(s)")
        self.report = report
        self.errors = errors


def fetch_all(companies: list[dict], captured_at: str, delay: float = 0.1
              ) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    """Fetch every company. Returns (roles, per-company counts, per-company failures)."""
    roles: list[dict] = []
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    for c in companies:
        try:
            got = fetch_company(c["company"], c["ats"], c["slug"], captured_at=captured_at)
        except FetchError as exc:
            failures[c["company"]] = str(exc)
            logger.error("%s: %s", c["company"], exc)
        else:
            roles.extend(got)
            counts[c["company"]] = len(got)
        time.sleep(delay)
    return roles, counts, failures


def _publish_report(report: str) -> None:
    """Log the validation report and, in GitHub Actions, show it on the run page."""
    logger.info("\n%s", report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


def run_daily_snapshot(
    db_path: Path | None = None,
    captured_at: str | None = None,
    delay: float = 0.1,
    snapshot_dir: Path | None = None,
    seed_path: Path | None = None,
    second_pass_delay: float = SECOND_PASS_DELAY_S,
) -> dict:
    """Fetch every seed company, validate the day, and store it only if it passes.

    Companies that fail are retried once more at the end of the run, after
    ``second_pass_delay`` seconds. Raises ``SnapshotRejected`` (writing nothing)
    if validation still fails.
    """
    captured_at = captured_at or dt.date.today().isoformat()
    companies = load_companies(seed_path)
    con = get_connection(db_path)
    try:
        loaded = load_history(con, snapshot_dir)   # rebuild prior history from committed Parquet
        sync_companies(con, companies)

        roles, counts, failures = fetch_all(companies, captured_at, delay)
        if failures:
            logger.warning("second pass in %.0fs for %d failed compan%s: %s", second_pass_delay,
                           len(failures), "y" if len(failures) == 1 else "ies", ", ".join(failures))
            time.sleep(second_pass_delay)
            retry = [c for c in companies if c["company"] in failures]
            more_roles, more_counts, failures = fetch_all(retry, captured_at, delay)
            roles += more_roles
            counts.update(more_counts)
            for company in more_counts:
                logger.info("%s recovered on the second pass", company)
        prev_date, prev = previous_counts(con, captured_at)
        null_rate = sum(1 for r in roles if not r.get("published_at")) / len(roles) if roles else 0.0
        result = validate_snapshot(counts, failures, prev, null_rate)
        report = render_report(result, captured_at, prev_date)
        _publish_report(report)
        if not result.ok:
            raise SnapshotRejected(report, result.errors)

        written = write_snapshot(con, roles, captured_at)
        parquet = write_day_parquet(con, captured_at, snapshot_dir)  # persist today for future runs
    finally:
        con.close()

    logger.info("snapshot %s saved: %d roles from %d companies; history files=%d",
                captured_at, written, len(companies), loaded)
    return {
        "captured_at": captured_at,
        "companies": len(companies),
        "roles": written,
        "warnings": result.warnings,
        "history_files_loaded": loaded,
        "parquet": str(parquet),
    }


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        print(run_daily_snapshot())
    except SnapshotRejected as exc:
        # Picked up by the workflow's failure alert.
        Path("validation-errors.txt").write_text("\n".join(exc.errors), encoding="utf-8")
        logger.error("Snapshot BLOCKED — nothing was saved:\n- %s", "\n- ".join(exc.errors))
        sys.exit(1)
