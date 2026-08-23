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
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 25
_UA = {"User-Agent": "hiring-leaderboard (+https://github.com/)"}


def _get(url: str) -> Optional[dict | list]:
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers=_UA)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch failed for %s: %s", url, exc)
        return None


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
    if not data:
        return []
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
        return []
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
    if not isinstance(data, dict):
        return []
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
    """Fetch and normalize one company's open roles, stamped with a snapshot date."""
    fetcher = ATS_FETCHERS.get(ats.lower().strip())
    if fetcher is None:
        logger.warning("unknown ATS %r for %s", ats, company)
        return []
    captured_at = captured_at or dt.date.today().isoformat()
    roles = fetcher(company, slug)
    for r in roles:
        r["company"] = company
        r["ats"] = ats
        r["captured_at"] = captured_at
    return [r for r in roles if r.get("role_id") and r.get("title")]
