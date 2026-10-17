"""Adapters for the public job-board APIs (ATS platforms).

Each company posts through one of a few applicant-tracking systems, each with
its own public, unauthenticated JSON endpoint and its own schema. The adapters
here fetch one company's currently-open roles and normalize them to a single
shape so everything downstream is ATS-agnostic:

    {company, ats, role_id, title, department, team, location,
     is_remote, url, published_at, captured_at}

``published_at`` is best-effort (each ATS exposes it differently, and some
omit it). It is *not* how "new roles" are detected — that comes from diffing
dated snapshots by ``role_id`` in the warehouse — so a missing value is fine.

A fetch either returns the board's real contents or raises ``FetchError``.
It never degrades to an empty list: an empty list means the board genuinely
has no openings, so a timeout must not be able to masquerade as one.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 25
MAX_ATTEMPTS = 4             # 1 try + 3 retries
BACKOFF_S = 2.0              # waits 2s, 4s, 8s between attempts
MAX_RETRY_AFTER_S = 60       # cap on a server-requested Retry-After
# Worth retrying: rate limits, timeouts, and server-side errors. Anything else
# (404 board renamed, 403, ...) won't fix itself, so it fails immediately.
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_UA = {"User-Agent": "hiring-leaderboard (+https://github.com/)"}


class FetchError(Exception):
    """A job board couldn't be fetched, even after retries."""


def _get(url: str) -> dict | list:
    """GET a JSON document, retrying transient failures with backoff."""
    last = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = BACKOFF_S * 2 ** (attempt - 1)
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers=_UA)
        except requests.RequestException as exc:  # timeout, DNS, connection reset
            last = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = "response was not valid JSON"
            elif r.status_code in _TRANSIENT_STATUS:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(int(retry_after), MAX_RETRY_AFTER_S)
            else:
                raise FetchError(f"HTTP {r.status_code} from {url}")
        if attempt < MAX_ATTEMPTS:
            logger.warning("attempt %d/%d failed for %s (%s); retrying in %.0fs",
                           attempt, MAX_ATTEMPTS, url, last, wait)
            time.sleep(wait)
    raise FetchError(f"{url} failed after {MAX_ATTEMPTS} attempts: {last}")


def _iso(value) -> Optional[str]:
    """Normalize a variety of timestamp encodings to a YYYY-MM-DD date string."""
    if value is None or value == "":
        return None
    # Epoch milliseconds (Lever) or seconds.
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e12 else value
        try:
            return dt.datetime.fromtimestamp(secs, tz=dt.timezone.utc).date().isoformat()
        except (ValueError, OSError):
            return None
    # ISO-8601 string (Greenhouse, Ashby).
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10] or None


def _is_remote(location: Optional[str]) -> bool:
    return bool(location) and "remote" in location.lower()


# ---------------------------------------------------------------------------
# Greenhouse — use the /departments endpoint so each role carries its team
# without downloading the heavy full-content payload.
# ---------------------------------------------------------------------------
def fetch_greenhouse(company: str, slug: str) -> list[dict]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/departments")
    if not isinstance(data, dict) or "departments" not in data:
        raise FetchError(f"unexpected Greenhouse payload for {company!r}")
    roles: dict[int, dict] = {}
    for dept in data.get("departments", []):
        dept_name = dept.get("name") or None
        for job in dept.get("jobs", []):
            rid = job.get("id")
            if rid is None or rid in roles:
                continue
            location = (job.get("location") or {}).get("name")
            roles[rid] = {
                "role_id": str(rid),
                "title": job.get("title"),
                "department": dept_name,
                "team": None,
                "location": location,
                "is_remote": _is_remote(location),
                "url": job.get("absolute_url"),
                "published_at": _iso(job.get("first_published") or job.get("updated_at")),
            }
    return list(roles.values())


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------
def fetch_lever(company: str, slug: str) -> list[dict]:
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        raise FetchError(f"unexpected Lever payload for {company!r}")
    roles = []
    for job in data:
        cats = job.get("categories") or {}
        location = cats.get("location")
        roles.append(
            {
                "role_id": str(job.get("id")),
                "title": job.get("text"),
                "department": cats.get("department") or cats.get("team"),
                "team": cats.get("team"),
                "location": location,
                "is_remote": _is_remote(location) or (cats.get("commitment") == "Remote"),
                "url": job.get("hostedUrl"),
                "published_at": _iso(job.get("createdAt")),
            }
        )
    return roles


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------
def fetch_ashby(company: str, slug: str) -> list[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(data, dict) or "jobs" not in data:
        raise FetchError(f"unexpected Ashby payload for {company!r}")
    roles = []
    for job in data.get("jobs", []):
        location = job.get("location")
        roles.append(
            {
                "role_id": str(job.get("id")),
                "title": job.get("title"),
                "department": job.get("department"),
                "team": job.get("team"),
                "location": location,
                "is_remote": bool(job.get("isRemote")) or _is_remote(location),
                "url": job.get("jobUrl") or job.get("applyUrl"),
                "published_at": _iso(job.get("publishedAt")),
            }
        )
    return roles


ATS_FETCHERS: dict[str, Callable[[str, str], list[dict]]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def fetch_company(company: str, ats: str, slug: str, captured_at: Optional[str] = None) -> list[dict]:
    """Fetch and normalize one company's open roles, stamped with a snapshot date.

    Raises ``FetchError`` if the board can't be read; an empty list means the
    board was read successfully and has no openings.
    """
    fetcher = ATS_FETCHERS.get(ats.lower().strip())
    if fetcher is None:
        raise FetchError(f"unknown ATS {ats!r} for {company!r}")
    captured_at = captured_at or dt.date.today().isoformat()
    roles = fetcher(company, slug)
    for r in roles:
        r["company"] = company
        r["ats"] = ats
        r["captured_at"] = captured_at
    return [r for r in roles if r.get("role_id") and r.get("title")]
