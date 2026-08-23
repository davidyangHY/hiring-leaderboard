"""Export dbt marts to static JSON the frontend reads.

The site is fully static: this writes small JSON files that the Next.js app
loads and filters client-side (the timeframe slider sums new_roles over the
chosen date range). Runs at the end of the daily pipeline, after `dbt build`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import duckdb

from src.classify import classify_seniority

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/hiring.duckdb")
DEFAULT_OUT_DIR = Path("web/data")


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(db_path: Path | None = None, out_dir: Path | None = None) -> dict:
    con = duckdb.connect(str(db_path or DEFAULT_DB_PATH), read_only=True)
    out = Path(out_dir or DEFAULT_OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    # Core series — compact keys keep the payload small.
    daily = con.execute(
        """select posted_date, company, function, new_roles
           from mart_new_roles_daily
           where new_roles > 0 order by posted_date""").fetchall()
    daily_json = [
        {"d": d.isoformat(), "c": c, "f": f, "n": int(n)} for d, c, f, n in daily
    ]

    companies = _rows(con, """
        select company, ats, headcount, hq, notes, open_roles
        from mart_company_summary order by company""")

    # Per-role list for the company drill-down. Seniority is derived here from
    # the title. Compact keys: company, function, seniority, title, date, url.
    roles = con.execute(
        """select company, function, title, url, posted_date
           from mart_role_list order by posted_date desc""").fetchall()
    roles_json = [
        {"c": c, "f": f, "s": classify_seniority(t), "t": t,
         "d": d.isoformat(), "u": u}
        for c, f, t, u, d in roles
    ]

    functions = [r[0] for r in con.execute(
        "select distinct function from mart_new_roles_daily order by 1").fetchall()]

    dmin, dmax, last_snap, n_roles = con.execute("""
        select min(posted_date), max(posted_date),
               (select max(captured_at) from role_snapshots),
               (select count(*) from mart_company_summary)
        from mart_new_roles_daily""").fetchone()

    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "last_snapshot": str(last_snap),
        "date_min": str(dmin),
        "date_max": str(dmax),
        "total_companies": len(companies),
        "total_open_roles": sum(c["open_roles"] for c in companies),
        "functions": functions,
    }

    (out / "new_roles_daily.json").write_text(json.dumps(daily_json, separators=(",", ":")))
    (out / "companies.json").write_text(json.dumps(companies, separators=(",", ":"), default=str))
    (out / "roles.json").write_text(json.dumps(roles_json, separators=(",", ":"), default=str))
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    con.close()

    logger.info("exported %d daily rows, %d roles, %d companies to %s",
                len(daily_json), len(roles_json), len(companies), out)
    return {"daily_rows": len(daily_json), "roles": len(roles_json),
            "companies": len(companies), **meta}


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print(json.dumps(export(), indent=2, default=str))
