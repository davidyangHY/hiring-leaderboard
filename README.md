# 📊 The Hiring Leaderboard

**See where tech is *actually* hiring — live.** Which companies are opening the most new roles right now, by function and relative to their size. Refreshes itself every day.

![daily pipeline](https://github.com/davidyangHY/hiring-leaderboard/actions/workflows/daily-snapshot.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![dbt](https://img.shields.io/badge/dbt-duckdb-FF694B?logo=dbt&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)

### ▶ [**Live demo**](https://hiring-leaderboard.vercel.app) &nbsp;·&nbsp; 103 companies &nbsp;·&nbsp; updated daily

<!-- Tip: add a screenshot here for extra punch → ![screenshot](docs/screenshot.png) -->

---

## Why

Job boards show you *listings*. This shows you **momentum** — who's ramping up, in which roles, and whether a 200-person startup is out-hiring a 5,000-person giant *per head*. All on one screen.

## What you can do

- 📈 **Leaderboard of new roles** over any timeframe (drag the slider)
- 🧩 **Filter** by function (Engineering · Product · Data · Design · …) and company size
- ⚖️ **Rank by raw count or % of headcount** — who's growing fastest for their size
- 🔍 **Click a company** → its open roles by seniority, linking straight to each posting
- 🔄 **Runs itself** — a daily pipeline keeps the data fresh with zero touch

## How it works

```
103 job boards  →  normalize  →  classify  →  daily snapshot (CDC)  →  dbt models  →  static JSON  →  site
```

- **Ingest** public ATS APIs (Greenhouse · Lever · Ashby) through one pluggable adapter layer
- **Detect *new* roles** by diffing daily snapshots — not naïve publish dates — with history persisted as Parquet (source of truth) and DuckDB as a derived cache
- **Model** with **dbt** — staging → intermediate → marts, with tests
- **Serve** as static JSON filtered entirely in-browser (no backend); **orchestrated** by a daily **GitHub Actions** cron that commits the refresh and auto-deploys

## Tech

`Python` · `DuckDB` · `dbt` · `GitHub Actions` · `vanilla JS` · `Vercel`

## Run locally

```bash
pip install -r requirements.txt
python -m src.snapshot                              # fetch + classify + snapshot
cd dbt_project && dbt build --profiles-dir . && cd ..   # model + test
python -m src.export_json                           # export JSON for the site
python -m http.server 8000 --directory web          # open http://localhost:8000
```

---

<sub>ℹ️ **Early data:** until snapshot history accumulates, new-role counts are estimated from posting dates and can overstate frequent re-posters; accuracy sharpens daily as change-data-capture takes over.</sub>
