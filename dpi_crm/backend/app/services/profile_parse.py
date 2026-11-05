"""Read name, headline, employer and start date from a profile's raw text.

``get_person_profile`` returns the text LinkedIn rendered, in the account's
language. Structure is used where it exists (line order, the experience
section's company links); the start date is the one place where words must be
read, so months are matched against an explicit per-locale table. A locale
missing from it leaves ``company_since`` empty and keeps the raw text.
"""

import datetime as dt
import re
from typing import Any

# Month names as LinkedIn abbreviates them in the experience dates.
MONTHS: dict[str, int] = {
    # es
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sept": 9,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
    # en
    "jan": 1,
    "apr": 4,
    "aug": 8,
    "dec": 12,
}
_DATE_RANGE = re.compile(r"^(?P<start>.+?)\s+[-–]\s+(?P<end>.+?)(\s+·\s+.*)?$")
_START = re.compile(r"(?P<month>[^\W\d_]+)\.?\s+(?:de\s+)?(?P<year>\d{4})|^(?P<only>\d{4})$")
_DEGREE = re.compile(r"^·\s")


def _lines(text: str | None) -> list[str]:
    return [s.strip() for s in (text or "").split("\n") if s.strip()]


def parse_start(text: str) -> dt.date | None:
    m = _START.search(text.strip().lower())
    if not m:
        return None
    if m.group("only"):
        return dt.date(int(m.group("only")), 1, 1)
    month = MONTHS.get(m.group("month").rstrip("."))
    return dt.date(int(m.group("year")), month, 1) if month else None


# Words for the duration LinkedIn prints next to a company ("3 años 10 meses").
_YEAR_WORDS = ("año", "años", "yr", "yrs", "year", "years")
_MONTH_WORDS = ("mes", "meses", "mo", "mos", "month", "months")
_DURATION_PART = re.compile(r"(\d+)\s+([^\W\d_]+)")


def parse_duration(text: str) -> tuple[int, int] | None:
    years = months = 0
    found = False
    for number, word in _DURATION_PART.findall(text.lower()):
        if word in _YEAR_WORDS:
            years, found = int(number), True
        elif word in _MONTH_WORDS:
            months, found = int(number), True
    return (years, months) if found else None


def _months_before(today: dt.date, years: int, months: int) -> dt.date:
    total = today.year * 12 + today.month - 1 - (years * 12 + months)
    return dt.date(total // 12, total % 12 + 1, 1)


def _current_position(
    experience: str, refs: list[dict[str, Any]], today: dt.date
) -> dict[str, Any]:
    lines = _lines(experience)
    # The first line is the section heading; the first entry follows.
    entry = lines[1:8]
    out: dict[str, Any] = {}
    for i, line in enumerate(entry):
        m = _DATE_RANGE.match(line)
        if m and re.search(r"\d{4}", m.group("start")):
            role_dates, role_start = line, parse_start(m.group("start"))
            before = entry[:i]
            break
    else:
        return out
    if len(before) >= 3:
        # Several roles at one employer: company, its total time there, then
        # the newest role. The role's dates are not the company's.
        out["company"] = before[0]
        out["title"] = before[-1]
        duration = parse_duration(before[1])
        out["company_since"] = _months_before(today, *duration) if duration else None
        out["company_since_text"] = f"{before[1]} · puesto actual: {role_dates}"
    else:
        out["company_since_text"] = role_dates
        out["company_since"] = role_start
        if before:
            out["title"] = before[0]
        if len(before) > 1:
            # "Cox · Jornada completa": the employer precedes the first separator.
            out["company"] = before[1].split(" · ")[0].strip()
    out.update(_company_id(out.get("company"), refs, grouped=len(before) >= 3))
    return out


def _company_id(
    company: str | None, refs: list[dict[str, Any]], grouped: bool = False
) -> dict[str, Any]:
    """LinkedIn's id for the current employer, from the experience links.

    For a single role, a link counts only when its text names that employer:
    an employer with no LinkedIn page has no link, and the first link would
    then be an older job's. Several roles at one employer sit under one
    company header, so the section's first company link is that employer's
    (measured: Thales 1951 and Michael Page 3476 whose link texts name a role).
    """
    if not company:
        return {}
    for ref in refs:
        if ref.get("kind") != "company":
            continue
        if grouped:
            m = re.search(r"/company/(\d+)/", ref.get("url") or "")
            return {"company_linkedin_id": m.group(1)} if m else {}
        if company.lower() not in (ref.get("text") or "").lower():
            continue
        m = re.search(r"/company/(\d+)/", ref.get("url") or "")
        if m:
            return {"company_linkedin_id": m.group(1)}
    return {}


def raw_for_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """What is kept of a profile read, to parse it again without LinkedIn."""
    sections = payload.get("sections") or {}
    return {
        "main_profile": (sections.get("main_profile") or "")[:4000],
        "experience": (sections.get("experience") or "")[:4000],
        "experience_refs": ((payload.get("references") or {}).get("experience") or [])[:10],
    }


def parse_profile(payload: dict[str, Any], today: dt.date | None = None) -> dict[str, Any]:
    """Parse a ``get_person_profile`` result, or a ``raw_for_storage`` dict."""
    sections = payload.get("sections") or payload
    refs = (
        (payload.get("references") or {}).get("experience") or payload.get("experience_refs") or []
    )
    main = _lines(sections.get("main_profile"))
    out: dict[str, Any] = {}
    if main:
        out["full_name"] = main[0]
        rest = [s for s in main[1:] if not _DEGREE.match(s) and s != "·"]
        if rest:
            out["headline"] = rest[0]
        if len(rest) > 1:
            out["location"] = rest[1]
    out.update(_current_position(sections.get("experience") or "", refs, today or dt.date.today()))
    return out
