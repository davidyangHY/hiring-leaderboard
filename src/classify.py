"""Classify a job posting into a canonical function.

ATS department fields are inconsistent (Ashby is clean: "Engineering"; Stripe
files roles under codes like "8811 Product Design"; Anduril/Palantir use team
codenames). So classification matches an ordered set of keyword rules against
the **title and department together**, and — crucially — order matters: more
specific functions are tested before broader ones, so "Data Engineer" lands in
Data and "Sales Engineer" in Sales before the generic "engineer" rule can claim
them.
"""

from __future__ import annotations

import re

# Canonical functions. The first four mirror the leaderboard's headline columns.
FUNCTIONS = [
    "Engineering", "Product", "Data", "Design",
    "Sales", "Marketing", "Finance", "People", "Legal", "Support", "Operations",
    "Other",
]

# (function, pattern) in priority order. First match wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Data", re.compile(
        r"data scientist|data engineer|\bml engineer|machine learning|\bmlops\b|"
        r"research scientist|research engineer|applied scientist|deep learning|"
        r"data analyst|analytics engineer|business intelligence|computer vision|"
        r"\bnlp\b|quantitative research", re.I)),
    ("Design", re.compile(
        r"designer|\bux\b|\bui/ux\b|user experience|user research|product design|"
        r"brand design|graphic design|design lead|creative director|illustrat", re.I)),
    ("Product", re.compile(
        r"product manager|product management|product owner|group product|"
        r"principal product|head of product|product lead|\bcpo\b", re.I)),
    ("Sales", re.compile(
        r"\bsales\b|account executive|account manager|business development|\bbdr\b|"
        r"\bsdr\b|solutions engineer|sales engineer|solutions architect|"
        r"solutions consultant|\brevenue\b|go.to.market|\bgtm\b|partnerships|"
        r"account director|customer success|renewals", re.I)),
    ("Marketing", re.compile(
        r"marketing|growth marketing|content marketing|communications|demand gen|"
        r"brand marketing|community manager|social media|copywriter|\bseo\b|"
        r"public relations|events manager", re.I)),
    ("Engineering", re.compile(
        r"engineer|developer|software|\bswe\b|devops|\bsre\b|site reliability|"
        r"infrastructure|back.?end|front.?end|full.?stack|platform|security|"
        r"technical staff|architect|programmer|\bqa\b|test engineer|firmware|"
        r"hardware|embedded|\bsystems\b|networking|technical lead|\bcto\b", re.I)),
    ("Finance", re.compile(
        r"finance|accounting|accountant|controller|fp&a|treasury|\btax\b|audit|"
        r"financial|bookkeep|payroll", re.I)),
    ("People", re.compile(
        r"recruit|talent|\bpeople\b|human resources|\bhr\b|sourcer|compensation|"
        r"benefits|workplace experience", re.I)),
    ("Legal", re.compile(
        r"legal|counsel|compliance|privacy|policy|regulatory|paralegal", re.I)),
    ("Support", re.compile(
        r"\bsupport\b|customer experience|technical support|help desk|customer care", re.I)),
    ("Operations", re.compile(
        r"operations|\bops\b|program manager|project manager|supply chain|logistics|"
        r"manufactur|procurement|facilities|administrative|executive assistant|"
        r"business operations|\bstrategy\b|chief of staff|general manager", re.I)),
]


def classify_function(title: str | None, department: str | None = None) -> str:
    """Return the canonical function for a role from its title (+ department)."""
    hay = f"{title or ''} {department or ''}"
    for function, pattern in _RULES:
        if pattern.search(hay):
            return function
    return "Other"


# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------
# Keyed off unambiguous *level* words only. Deliberately no generic "Manager"
# bucket: "Product Manager" / "Account Manager" are IC roles, not management,
# so keying on "manager" would mislabel them. Order: most specific first.
SENIORITY_LEVELS = ["Intern", "Junior", "Senior", "Staff+", "Leadership", "Unspecified"]

_SENIORITY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Intern", re.compile(r"\bintern(?:ship)?\b|\bco-?op\b", re.I)),
    ("Staff+", re.compile(r"\b(?:staff|principal|distinguished|fellow)\b", re.I)),
    ("Leadership", re.compile(
        r"\b(?:director|vp|svp|evp|vice president|chief|c[etofim]o|head of|president)\b", re.I)),
    ("Senior", re.compile(r"\b(?:senior|sr\.?|lead)\b", re.I)),
    ("Junior", re.compile(
        r"\b(?:junior|jr\.?|entry[- ]?level|new[- ]?grad(?:uate)?|graduate|associate|apprentice|early[- ]career)\b", re.I)),
]


def classify_seniority(title: str | None) -> str:
    """Return a coarse seniority level from a job title."""
    hay = title or ""
    for level, pattern in _SENIORITY_RULES:
        if pattern.search(hay):
            return level
    return "Unspecified"


if __name__ == "__main__":  # pragma: no cover
    samples = [
        ("Senior Data Engineer", "Data Platform"),
        ("Staff Software Engineer, Backend", "Infrastructure"),
        ("Product Designer", "8811 Product Design"),
        ("Group Product Manager", "Product"),
        ("Sales Engineer, Enterprise", "Revenue"),
        ("Account Executive (EMEA)", "1185 Account Executives"),
        ("Growth Marketing Lead", "Marketing"),
        ("Recruiter, Technical", "People & Talent"),
        ("Member of Technical Staff", "Research"),
        ("Office Operations Associate", "Business Operations"),
    ]
    for t, d in samples:
        print(f"  {classify_function(t, d):12} <- {t}  [{d}]")
