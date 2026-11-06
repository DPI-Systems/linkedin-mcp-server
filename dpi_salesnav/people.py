"""Company -> people via Sales Navigator lead search.

One navigation per results page. The people come from the
``salesApiLeadSearch`` JSON the search page loads by itself.
"""

import asyncio
import logging
import os
import random
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

from linkedin_mcp_server.core.exceptions import AccountRestrictedError, RateLimitError
from linkedin_mcp_server.core.throttle import throttle_evidence, throttled_count

from dpi_salesnav.capture import SalesApiRecorder

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
LEAD_SEARCH_PATH = "/sales-api/salesApiLeadSearch"
_RESPONSE_TIMEOUT = 20.0
# Pause between result pages, as a person paging through results would.
_PAGE_PAUSE = (20.0, 40.0)
# Least time between two Sales Navigator searches, across calls: LinkedIn
# answered a burst of searches with human-verification checks, each one sooner
# than the last. A pause, not a refusal: a search that comes too soon waits.
MIN_SEARCH_GAP_ENV = "DPI_SALESNAV_MIN_SEARCH_GAP_SECONDS"
_SEARCH_GAP = 20.0
_SEARCH_JITTER = 10.0
_last_search: float | None = None

_PROFILE_ID = re.compile(r"urn:li:fs_salesProfile:\(([^,]+),([^,]+),([^)]+)\)")
_SAFE_KEYWORDS = re.compile(r"[^\w\s\-.&]", re.UNICODE)


# Sales Navigator's own filter ids, read back from its search metadata.
SENIORITY_IDS = {
    "training": 100,
    "entry": 110,
    "senior": 120,
    "strategic": 130,
    "entry_manager": 200,
    "experienced_manager": 210,
    "director": 220,
    "vp": 300,
    "cxo": 310,
    "owner": 320,
}
FUNCTION_IDS = {
    "accounting": 1,
    "administrative": 2,
    "arts_design": 3,
    "business_development": 4,
    "community_social": 5,
    "consulting": 6,
    "education": 7,
    "engineering": 8,
    "entrepreneurship": 9,
    "finance": 10,
    "healthcare": 11,
    "human_resources": 12,
    "it": 13,
    "legal": 14,
    "marketing": 15,
    "media_communication": 16,
    "military_protective": 17,
    "operations": 18,
    "product_management": 19,
    "program_project_management": 20,
    "purchasing": 21,
    "quality_assurance": 22,
    "real_estate": 23,
    "research": 24,
    "sales": 25,
    "customer_success": 26,
}
SPAIN_GEO_ID = "105646813"


@dataclass(frozen=True)
class LeadFilters:
    keywords: str | None = None
    titles: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    posted_recently: bool = False
    changed_jobs_recently: bool = False

    def __post_init__(self) -> None:
        _check_names("seniority", self.seniority, SENIORITY_IDS)
        _check_names("functions", self.functions, FUNCTION_IDS)
        bad = [r for r in self.regions if not str(r).isdigit()]
        if bad:
            raise ValueError(f"regions must be numeric LinkedIn geo ids, got {bad}")


def _check_names(field: str, names: tuple[str, ...], table: dict[str, int]) -> None:
    unknown = [n for n in names if n not in table]
    if unknown:
        raise ValueError(f"Unknown {field} {unknown}; use any of: {', '.join(table)}")


def _clean_text(text: str) -> str:
    return " ".join(_SAFE_KEYWORDS.sub(" ", text).split())


def _filter(kind: str, values: list[str]) -> str:
    items = ",".join(f"({v},selectionType:INCLUDED)" for v in values)
    return f"(type:{kind},values:List({items}))"


def lead_search_url(
    company_id: str | None, filters: LeadFilters | None = None, page: int = 1
) -> str:
    f = filters or LeadFilters()
    clauses = []
    if company_id:
        company = quote(f"urn:li:organization:{company_id}", safe="")
        clauses.append(_filter("CURRENT_COMPANY", [f"id:{company}"]))
    titles = [t for t in (_clean_text(t) for t in f.titles) if t]
    if titles:
        clauses.append(_filter("CURRENT_TITLE", [f"text:{t}" for t in titles]))
    if f.seniority:
        clauses.append(
            _filter("SENIORITY_LEVEL", [f"id:{SENIORITY_IDS[n]}" for n in f.seniority])
        )
    if f.functions:
        clauses.append(
            _filter("FUNCTION", [f"id:{FUNCTION_IDS[n]}" for n in f.functions])
        )
    if f.regions:
        clauses.append(_filter("REGION", [f"id:{r}" for r in f.regions]))
    if f.posted_recently:
        clauses.append(_filter("POSTED_ON_LINKEDIN", ["id:RPOL"]))
    if f.changed_jobs_recently:
        clauses.append(_filter("RECENTLY_CHANGED_JOBS", ["id:RPC"]))
    parts = [f"filters:List({','.join(clauses)})"] if clauses else []
    keywords = _clean_text(f.keywords or "")
    if keywords:
        parts.insert(0, f"keywords:{keywords}")
    query = quote("(" + ",".join(parts) + ")", safe="():,")
    suffix = f"&page={page}" if page > 1 else ""
    return f"https://www.linkedin.com/sales/search/people?query={query}{suffix}"


def _tenure(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {"years": value.get("numYears", 0), "months": value.get("numMonths", 0)}


def _started_on(value: Any) -> str | None:
    if not isinstance(value, dict) or "year" not in value:
        return None
    month = value.get("month")
    return f"{value['year']}-{month:02d}" if month else str(value["year"])


def lead_from_hit(hit: dict[str, Any], company_id: str | None) -> dict[str, Any]:
    positions = [p for p in hit.get("currentPositions") or [] if isinstance(p, dict)]
    at_company = [
        p
        for p in positions
        if company_id and str(p.get("companyUrn", "")).endswith(f":{company_id}")
    ]
    main = (at_company or positions or [{}])[0]
    badges = {
        b.get("id"): b.get("displayValue")
        for b in hit.get("spotlightBadges") or []
        if isinstance(b, dict)
    }
    profile_id = auth_type = auth_token = None
    if m := _PROFILE_ID.match(hit.get("entityUrn", "")):
        profile_id, auth_type, auth_token = m.groups()
    return {
        "full_name": (hit.get("fullName") or "").strip(),
        "title": (main.get("title") or "").strip() or None,
        "company": main.get("companyName"),
        "tenure_at_company": _tenure(main.get("tenureAtCompany")),
        "tenure_at_position": _tenure(main.get("tenureAtPosition")),
        "position_started_on": _started_on(main.get("startedOn")),
        "other_current_positions": [
            {"title": p.get("title"), "company": p.get("companyName")}
            for p in positions
            if p is not main
        ],
        "location": hit.get("geoRegion"),
        "seniority": [s.get("displayName") for s in hit.get("seniorityV2s") or []],
        "degree": hit.get("degree"),
        "recent_activity": badges.get("POSTED_ON_LINKEDIN"),
        "changed_jobs_recently": "RECENTLY_HIRED" in badges or "CHANGED_JOBS" in badges,
        "promoted_recently": "RECENTLY_PROMOTED" in badges,
        "badges": badges,
        "summary": hit.get("summary"),
        "open_link": hit.get("openLink"),
        "premium": hit.get("premium"),
        "pending_invitation": hit.get("pendingInvitation"),
        "saved": hit.get("saved"),
        "member_urn": hit.get("objectUrn"),
        "sales_profile_id": profile_id,
        "sales_nav_url": (
            f"https://www.linkedin.com/sales/lead/{profile_id},{auth_type},{auth_token}"
            if profile_id
            else None
        ),
    }


def region_breakdown(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Where the results live, by Sales Navigator region, with geo ids."""
    out = []
    for flt in (payload.get("metadata") or {}).get("filters") or []:
        agg = flt.get("aggregatedFilterMetadata") if isinstance(flt, dict) else None
        if not isinstance(agg, dict) or agg.get("type") != "GEOGRAPHY":
            continue
        for group in agg.get("values") or []:
            if group.get("type") != "REGION":
                continue
            for v in group.get("values") or []:
                count = str(v.get("displayCount", "")).strip()
                out.append(
                    {
                        "id": v.get("id"),
                        "name": v.get("displayValue"),
                        "count": int(count) if count.isdigit() else count or None,
                    }
                )
    return out


class ChallengeShown(AccountRestrictedError):
    """LinkedIn asked this session to prove it is human."""


def _raise_if_challenged(recorder: SalesApiRecorder) -> None:
    if recorder.challenge:
        raise ChallengeShown(
            "Sales Navigator sent no results while LinkedIn ran a "
            f"human-verification check ({recorder.challenge}), which may be "
            "invisible. Pause this account for a while instead of retrying; "
            "if the browser window shows a check, solve it by hand."
        )


def _raise_if_throttled() -> None:
    if throttled_count():
        raise RateLimitError(
            throttle_evidence() or "LinkedIn throttled the Sales Navigator search."
        )


async def page_diagnosis(page: Any, recorder: SalesApiRecorder) -> str:
    """What the page showed when the data never came, for the error message."""
    try:
        title = await page.title()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception as e:
        title, text = "?", f"(could not read the page: {e})"
    statuses = [
        f"{urlparse(r['url']).path.rsplit('/', 1)[-1]}={r['status']}"
        for r in recorder.records
    ]
    hosts = Counter(entry.split("/", 1)[0] for entry in recorder.seen)
    linkedin = [
        e
        for e in recorder.seen
        if e.startswith("www.linkedin.com/") and "/sales-api/" not in e
    ]
    head = " ".join(str(text).split())[:300]
    return (
        f"page {page.url!r}, title {title!r}, text {head!r}, "
        f"sales-api responses {statuses[-15:]}, "
        f"all responses by host {dict(hosts.most_common(8))}, "
        f"other linkedin responses {linkedin[-15:]}"
    )


def _search_gap() -> float:
    try:
        return max(0.0, float(os.environ.get(MIN_SEARCH_GAP_ENV, _SEARCH_GAP)))
    except ValueError:
        return _SEARCH_GAP


async def wait_for_search_turn() -> float:
    """Sleep until the gap since the previous search has passed; return the wait."""
    global _last_search
    loop = asyncio.get_running_loop()
    waited = 0.0
    if _last_search is not None:
        gap = _search_gap()
        due = _last_search + gap + random.uniform(0.0, _SEARCH_JITTER if gap else 0.0)
        waited = max(0.0, due - loop.time())
        if waited:
            logger.info("Waiting %.0f s before the next Sales Navigator search", waited)
            await asyncio.sleep(waited)
    _last_search = loop.time()
    return waited


async def load_sales_json(page: Any, url: str, path: str, what: str) -> dict[str, Any]:
    """Open ``url`` and return the newest ``path`` JSON its load brings.

    After a few searches Sales Navigator can show results without asking the
    network for them again; a reload makes it ask, as it would for a person
    pressing F5. One reload at most.
    """
    await wait_for_search_turn()
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        for attempt in range(2):
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _RESPONSE_TIMEOUT
            while loop.time() < deadline:
                await recorder.drain()
                if found := recorder.matching(path):
                    return found[-1]["json"]
                _raise_if_throttled()
                await asyncio.sleep(0.5)
            # Only now, with no data after the whole wait: LinkedIn may start
            # this check in the background on loads that work fine.
            _raise_if_challenged(recorder)
            if attempt == 0:
                logger.info("No %s data after load; reloading once", what)
                await page.reload(wait_until="domcontentloaded")
        raise RuntimeError(
            f"Sales Navigator {what} loaded no results data, even after a "
            "reload: " + await page_diagnosis(page, recorder)
        )
    finally:
        recorder.remove()
        await recorder.drain()


async def company_people(
    page: Any,
    company_id: str | None,
    *,
    filters: LeadFilters | None = None,
    max_results: int = PAGE_SIZE,
) -> dict[str, Any]:
    leads: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    total: int | None = None
    pages_read = 0
    page_number = 1
    while len(leads) < max_results:
        if page_number > 1:
            await asyncio.sleep(random.uniform(*_PAGE_PAUSE))
        url = lead_search_url(company_id, filters, page_number)
        payload = await load_sales_json(page, url, LEAD_SEARCH_PATH, "lead search")
        _raise_if_throttled()
        pages_read += 1
        if page_number == 1:
            regions = region_breakdown(payload)
        total = (payload.get("paging") or {}).get("total", total)
        hits = payload.get("elements") or []
        leads.extend(lead_from_hit(h, company_id) for h in hits if isinstance(h, dict))
        if len(hits) < PAGE_SIZE:
            break
        page_number += 1
    return {
        "company_id": company_id,
        "search_url": lead_search_url(company_id, filters),
        "total_available": total,
        "pages_read": pages_read,
        "count": min(len(leads), max_results),
        "region_breakdown": regions,
        "leads": leads[:max_results],
    }


ACCOUNT_SEARCH_PATH = "/sales-api/salesApiAccountSearch"
_SALES_COMPANY_URN = re.compile(r"urn:li:fs_salesCompany:(\d+)")


def account_search_url(name: str) -> str:
    cleaned = _SAFE_KEYWORDS.sub(" ", name).strip()
    query = quote(f"(keywords:{cleaned})", safe="():,")
    return f"https://www.linkedin.com/sales/search/company?query={query}"


def account_from_hit(hit: dict[str, Any]) -> dict[str, Any] | None:
    match = _SALES_COMPANY_URN.search(str(hit.get("entityUrn", "")))
    if not match:
        return None
    company_id = match.group(1)
    location = next(
        (
            hit[k]
            for k in ("location", "headquarters", "geoRegion", "hqLocation")
            if isinstance(hit.get(k), str) and hit[k].strip()
        ),
        None,
    )
    description = hit.get("description")
    return {
        "company_id": company_id,
        "name": hit.get("companyName"),
        "industry": hit.get("industry"),
        "employees": hit.get("employeeCountRange") or hit.get("employeeDisplayCount"),
        "location": location,
        "description": description[:300] if isinstance(description, str) else None,
        "sales_nav_url": f"https://www.linkedin.com/sales/company/{company_id}",
        "linkedin_url": f"https://www.linkedin.com/company/{company_id}/",
    }


async def search_companies(page: Any, name: str) -> dict[str, Any]:
    """Every company Sales Navigator's account search returns for ``name``."""
    payload = await load_sales_json(
        page,
        account_search_url(name),
        ACCOUNT_SEARCH_PATH,
        f"account search for '{name}'",
    )
    companies = [
        a
        for a in (account_from_hit(h) for h in payload.get("elements") or [])
        if a is not None
    ]
    return {
        "query": name,
        "total_available": (payload.get("paging") or {}).get("total"),
        "count": len(companies),
        "companies": companies,
    }
