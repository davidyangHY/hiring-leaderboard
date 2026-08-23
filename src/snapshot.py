"""Daily snapshot + change-data-capture into DuckDB.

Each run fetches every tracked company's currently-open roles and writes them as
one dated snapshot into ``role_snapshots``. Because we keep every day's snapshot
and the primary key is ``(captured_at, company, role_id)``, "new roles in a
window" is later derived by first-seen date:

    first_seen(role) = MIN(captured_at) over its (company, role_id)

so a role counts as *new* in the window that contains its first snapshot. Closed
roles simply stop appearing — their last snapshot marks when they went away.

Running twice in one day is idempotent (INSERT OR IGNORE on the day's key).
The ``companies`` dim table mirrors the seed so downstream joins (e.g. roles per
1k employees) don't need the CSV.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import duckdb

from src.ats import fetch_company
from src.classify import classify_function
from src.seed import load_companies

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/hiring.duckdb")
# Committed, append-only history. The DuckDB file is a derived cache rebuilt
# from these each run, so snapshot history survives ephemeral CI runners.
SNAPSHOT_DIR = Path("data/snapshots")

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


def write_snapshot(con: duckdb.DuckDBPyConnection, roles: list[dict], captured_at: str) -> int:
    """Insert one day's normalized+classified roles; idempotent for that day."""
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
    if not rows:
        return 0
    before = con.execute("SELECT COUNT(*) FROM role_snapshots").fetchone()[0]
    con.executemany(
        """INSERT OR IGNORE INTO role_snapshots (
             captured_at, company, ats, role_id, title, department, function,
             team, location, is_remote, url, published_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    after = con.execute("SELECT COUNT(*) FROM role_snapshots").fetchone()[0]
    return after - before


def run_daily_snapshot(
    db_path: Path | None = None,
    captured_at: str | None = None,
    delay: float = 0.1,
) -> dict:
    """Fetch every seed company's open roles and store today's snapshot."""
    captured_at = captured_at or dt.date.today().isoformat()
    companies = load_companies()
    con = get_connection(db_path)
    loaded = load_history(con)          # rebuild prior history from committed Parquet
    sync_companies(con, companies)

    all_roles: list[dict] = []
    failed: list[str] = []
    for c in companies:
        roles = fetch_company(c["company"], c["ats"], c["slug"], captured_at=captured_at)
        if not roles:
            failed.append(c["company"])
        all_roles.extend(roles)
        time.sleep(delay)

    inserted = write_snapshot(con, all_roles, captured_at)
    parquet = write_day_parquet(con, captured_at)   # persist today for future runs
    stats = {
        "captured_at": captured_at,
        "companies": len(companies),
        "failed": failed,
        "roles_fetched": len(all_roles),
        "rows_inserted": inserted,
        "history_files_loaded": loaded,
        "parquet": str(parquet),
    }
    logger.info("snapshot %s: %d roles from %d companies (%d new rows), %d failed; history files=%d",
                captured_at, len(all_roles), len(companies), inserted, len(failed), loaded)
    con.close()
    return stats


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print(run_daily_snapshot())
