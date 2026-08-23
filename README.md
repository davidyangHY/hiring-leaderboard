# The Hiring Leaderboard

**A live map of where tech is actually hiring** — which companies are opening the
most *new* roles right now, overall, by function, and relative to their size.

> Portfolio data-engineering project: a self-updating pipeline that turns dozens
> of company job boards into a single ranked, filterable dataset.

---

## The problem it solves

Job seekers and market-watchers can see *individual* job postings everywhere
(LinkedIn, Indeed, each company's careers page) — but there's no clean answer to
the question that actually matters:

> **Which companies are ramping up hiring right now — and who's growing fastest for their size?**

A single open role tells you nothing about momentum. **New roles over a time
window** do: they show which companies are scaling, in which functions, and
whether a 200-person startup is out-hiring a 5,000-person incumbent relative to
headcount. This tool answers that in one screen.

**Who it's for**
- **Job seekers** — where to aim: who's hiring hard in Engineering / Data / Product / Design *this month*.
- **Market & talent watchers** — job-posting velocity as a real growth signal.

---

## What it shows

The dashboard lets you slice one dataset four ways at once:

- **Timeframe** — a slider / presets (7d → 1yr): new roles posted in that window.
- **Function** — Engineering, Product, Data, Design, Sales, Marketing, Ops, and more.
- **Company size** — All / Startup (<500) / Scaleup (500–5k) / Enterprise (5k+).
- **Metric** — raw **new roles**, or **% of headcount** (openings relative to team size — the "who's ramping hardest" view).

---

## Architecture

```
  Company job boards (Greenhouse / Lever / Ashby public APIs)
                        │
             ┌──────────▼───────────┐
             │  src/ats.py          │  fetch + normalize to one schema
             │  src/classify.py     │  title/dept → function
             └──────────┬───────────┘
                        │  daily
             ┌──────────▼───────────┐
             │  src/snapshot.py     │  write dated snapshot → DuckDB (CDC)
             └──────────┬───────────┘
                        │
             ┌──────────▼───────────┐
             │  dbt (dbt-duckdb)    │  staging → intermediate → marts + tests
             └──────────┬───────────┘
                        │
             ┌──────────▼───────────┐
             │  src/export_json.py  │  marts → static JSON
             └──────────┬───────────┘
                        │
             ┌──────────▼───────────┐
             │  web/ (static site)  │  loads JSON, filters in-browser
             └──────────────────────┘
```

Every stage is plain Python + SQL; the site is dependency-free static files.

---

## The data source

Companies post through a handful of **applicant-tracking systems (ATS)** that
expose **public, unauthenticated JSON** — the same feeds their own careers pages
consume. We read those directly:

| ATS | Endpoint | Fields used |
|-----|----------|-------------|
| **Greenhouse** | `boards-api.greenhouse.io/v1/boards/{slug}/departments` | title, department, location, `first_published` |
| **Ashby** | `api.ashbyhq.com/posting-api/job-board/{slug}` | title, department, team, location, `publishedAt` |
| **Lever** | `api.lever.co/v0/postings/{slug}` | title, categories, location, `createdAt` |

The tracked universe is a **curated seed list** ([`seed/companies.csv`](seed/companies.csv))
— currently **103 companies**, ~17.5k open roles. Each row is `company, ats, slug,
headcount, hq, notes`. Add one by finding its slug (it's in the careers URL) and
verifying:

```bash
python -m src.seed find <slug>     # detect which ATS a slug is on
python -m src.seed verify          # check every company still returns roles
```

### How "new roles" is measured (change-data-capture)

The APIs only return *currently-open* roles, so "new roles in a window" is
derived two ways:

1. **Snapshot diffing (ground truth, going forward).** Each day we store a full
   snapshot in `role_snapshots`, keyed by `(captured_at, company, role_id)`. A
   role's **first-seen date** = the earliest snapshot it appears in. It counts as
   *new* in whatever window contains that date. History accrues from the day the
   pipeline starts running.
2. **Publish-date bootstrap (for day one).** Because a from-scratch tool would
   otherwise start empty, we also read each role's ATS-reported publish date and
   use it as the effective posted-date when it's sane (see caveats). This gives a
   realistic time series immediately; snapshot diffing takes over as the
   authoritative signal over time.

### Function classification

Department fields are inconsistent (Ashby is clean; Greenhouse uses codes like
`8811 Product Design`; Anduril/Palantir use team codenames). [`src/classify.py`](src/classify.py)
maps **title + department** to a canonical function via ordered keyword rules —
specific before general, so *Data Engineer* → Data and *Sales Engineer* → Sales
before the generic "engineer" rule fires. On real data **~97% classify** into a
named function (only ~3% fall to "Other").

---

## Automation

The pipeline is built to run headless and unattended:

```bash
python -m src.snapshot                 # 1. fetch + classify + snapshot to DuckDB
cd dbt_project && dbt build            # 2. model + test
python -m src.export_json              # 3. export static JSON for the site
```

**Scheduled run (GitHub Actions, daily):** a cron workflow runs those three
steps, commits the refreshed `web/data/*.json`, which triggers the static host to
redeploy. No server, no manual step — the leaderboard updates itself and the
dataset's history deepens every day.

> **Status:** data pipeline + dbt + export + web UI are **implemented and running
> on real data**. The scheduled GitHub Actions cron and the hosting deploy are
> the next step (the pipeline already runs end-to-end from the CLI).

---

## Caveats & limitations

Honest about what this does and doesn't capture:

- **Coverage is ATS-limited.** Only Greenhouse / Lever / Ashby are supported.
  Big enterprises on **Workday** (Qualcomm, ASML), **Eightfold** (Qualcomm,
  Netflix), or bespoke sites (Google, Apple, Tesla, Meta) are **not included** —
  those platforms are JS-rendered and/or actively block automated access, so
  scraping them would be fragile and against their terms. The tracked set is
  deliberately the companies whose boards are *meant* to be consumed publicly.
  A **Workday adapter** is a planned extension (one adapter unlocks many
  enterprises).
- **Headcount is approximate.** The per-size and %-of-headcount views use
  best-estimate headcounts maintained in the seed CSV; refine them for precision.
- **The day-one "new roles" count is a publish-date estimate, and it overcounts.**
  This is the most important caveat. You cannot measure *genuinely* new roles from
  a single snapshot, so until our own daily history accumulates the tool bootstraps
  "new roles" from each posting's ATS publish date (`first_published` / `publishedAt`
  / `createdAt`). The problem: many companies **re-post or refresh** listings, which
  **bumps that date** — so a refreshed old role looks new. On large, high-churn
  boards this inflates the count badly (e.g. SpaceX showed ~214 roles "published" in
  7 days and ~29% of its whole board within 30 days — implausibly high for true
  net-new). Deduping repost-duplicates barely helps (~8%), because the dates
  themselves are the issue. **The fix is time, not a cleverer parse:** the
  authoritative signal is snapshot diffing — a role is *new* only when its ID first
  appears in *our* daily snapshots, which is immune to date-bumping. That history
  starts the day the scheduled pipeline begins running and the numbers self-correct
  (downward for re-posters) over ~1–2 weeks.
- **"New" ≠ "net new headcount."** A posting is an *opening*, not a confirmed
  hire; backfills and reposts are counted as roles.

---

## Tech stack

Python 3 · DuckDB · dbt (dbt-duckdb) · requests · pytest · ruff · static
HTML/CSS/JS front-end · (planned) GitHub Actions + static hosting.

---

## Local setup

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m src.snapshot          # fetch today's snapshot into data/hiring.duckdb
cd dbt_project && dbt build --profiles-dir . && cd ..
python -m src.export_json       # write web/data/*.json
python -m http.server 8000 --directory web   # open http://localhost:8000
```

---

## Project structure

```
├── seed/companies.csv          # curated tracked-company list (the seed)
├── src/
│   ├── ats.py                  # Greenhouse / Lever / Ashby adapters → one schema
│   ├── classify.py             # title + department → function
│   ├── seed.py                 # load / verify / find companies
│   ├── snapshot.py             # daily snapshot + CDC into DuckDB
│   └── export_json.py          # marts → static JSON
├── dbt_project/                # staging → intermediate → marts (+ tests)
├── web/                        # static dashboard (index.html, styles.css, app.js)
├── data/                       # DuckDB warehouse + exported JSON (gitignored)
└── archive/                    # earlier project iteration, parked
```
