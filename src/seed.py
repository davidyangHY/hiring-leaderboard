"""Load and verify the tracked-company seed list.

The seed (``seed/companies.csv``) is the human-curated universe of companies to
track. This module reads it, and its CLI helps you maintain it:

    # verify every company in the seed is live (returns roles)
    python -m src.seed verify

    # figure out which ATS/slug a new company uses before adding it
    python -m src.seed find stripe
    python -m src.seed find acme-corp
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from src.ats import ATS_FETCHERS, fetch_company

logger = logging.getLogger(__name__)

SEED_PATH = Path("seed/companies.csv")
REQUIRED_COLUMNS = {"company", "ats", "slug"}


def load_companies(path: Path | None = None) -> list[dict]:
    """Read the seed CSV into a list of row dicts, validating its shape."""
    path = Path(path or SEED_PATH)
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
        rows = []
        for row in reader:
            row = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            if not row.get("company") or not row.get("slug"):
                continue
            if "headcount" in row and row["headcount"]:
                try:
                    row["headcount"] = int(row["headcount"])
                except ValueError:
                    row["headcount"] = None
            rows.append(row)
    return rows


def verify(path: Path | None = None) -> None:
    """Hit every company's ATS and report role counts, flagging dead entries."""
    rows = load_companies(path)
    ok = 0
    print(f"Verifying {len(rows)} companies...\n")
    for row in rows:
        roles = fetch_company(row["company"], row["ats"], row["slug"])
        n = len(roles)
        status = "OK " if n else "DEAD"
        if n:
            ok += 1
        print(f"  [{status}] {row['company']:16} {row['ats']:11} {row['slug']:16} {n:>4} roles")
        time.sleep(0.1)
    print(f"\n{ok}/{len(rows)} live.")
    if ok < len(rows):
        print("Fix DEAD rows: check the slug in the careers URL, or the ATS may differ.")


def find(slug: str) -> None:
    """Probe all ATS providers for a slug — helps you add a new company."""
    print(f"Probing slug {slug!r} across ATS providers...\n")
    hits = []
    for ats in ATS_FETCHERS:
        roles = fetch_company(slug, ats, slug)
        if roles:
            hits.append((ats, len(roles)))
            print(f"  MATCH  {ats:11} -> {len(roles)} roles")
        time.sleep(0.1)
    if not hits:
        print("  No match. Find the slug in the company's careers URL, e.g.:")
        print("    boards.greenhouse.io/<slug> | jobs.lever.co/<slug> | jobs.ashbyhq.com/<slug>")
        print("  (Some companies use Workday/SmartRecruiters — not yet supported.)")
    else:
        best = max(hits, key=lambda h: h[1])
        print(f"\n  -> add row:  <Company>,{best[0]},{slug},<headcount>,<hq>,<notes>")


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = sys.argv[1:]
    if args and args[0] == "verify":
        verify()
    elif len(args) == 2 and args[0] == "find":
        find(args[1])
    else:
        print(__doc__)
