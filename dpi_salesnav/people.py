"""Company -> people via Sales Navigator lead search.

One navigation per results page. The people come from the
``salesApiLeadSearch`` JSON the search page loads by itself.
"""

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import quote

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.throttle import throttle_evidence, throttled_count

from dpi_salesnav.capture import SalesApiRecorder

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
LEAD_SEARCH_PATH = "/sales-api/salesApiLeadSearch"
_RESPONSE_TIMEOUT = 20.0
# Pause between result pages, as a person paging through results would.
_PAGE_PAUSE = (4.0, 9.0)

_PROFILE_ID = re.compile(r"urn:li:fs_salesProfile:\(([^,]+),([^,]+),([^)]+)\)")
_SAFE_KEYWORDS = re.compile(r"[^\w\s\-.&]", re.UNICODE)


def lead_search_url(company_id: str, keywords: str | None, page: int) -> str:
    company = quote(f"urn:li:organization:{company_id}", safe="")
    filters = (
        "filters:List((type:CURRENT_COMPANY,values:List("
        f"(id:{company},selectionType:INCLUDED))))"
    )
    parts = [filters]
    if keywords:
        cleaned = _SAFE_KEYWORDS.sub(" ", keywords).strip()
        if cleaned:
            parts.insert(0, f"keywords:{cleaned}")
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


def lead_from_hit(hit: dict[str, Any], company_id: str) -> dict[str, Any]:
    positions = [p for p in hit.get("currentPositions") or [] if isinstance(p, dict)]
    at_company = [
        p for p in positions if str(p.get("companyUrn", "")).endswith(f":{company_id}")
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


def _raise_if_throttled() -> None:
    if throttled_count():
        raise RateLimitError(
            throttle_evidence() or "LinkedIn throttled the Sales Navigator search."
        )


async def _load_page(page: Any, url: str) -> dict[str, Any] | None:
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESPONSE_TIMEOUT
        while loop.time() < deadline:
            await recorder.drain()
            if found := recorder.matching(LEAD_SEARCH_PATH):
                return found[-1]["json"]
            _raise_if_throttled()
            await asyncio.sleep(0.5)
        return None
    finally:
        recorder.remove()
        await recorder.drain()


async def company_people(
    page: Any,
    company_id: str,
    *,
    keywords: str | None = None,
    max_results: int = PAGE_SIZE,
) -> dict[str, Any]:
    leads: list[dict[str, Any]] = []
    total: int | None = None
    pages_read = 0
    page_number = 1
    while len(leads) < max_results:
        if page_number > 1:
            await asyncio.sleep(random.uniform(*_PAGE_PAUSE))
        url = lead_search_url(company_id, keywords, page_number)
        payload = await _load_page(page, url)
        _raise_if_throttled()
        if payload is None:
            if page_number == 1:
                raise RuntimeError(
                    "Sales Navigator search loaded no results data; "
                    f"the page ended at {page.url}"
                )
            break
        pages_read += 1
        total = (payload.get("paging") or {}).get("total", total)
        hits = payload.get("elements") or []
        leads.extend(lead_from_hit(h, company_id) for h in hits if isinstance(h, dict))
        if len(hits) < PAGE_SIZE:
            break
        page_number += 1
    return {
        "company_id": company_id,
        "search_url": lead_search_url(company_id, keywords, 1),
        "total_available": total,
        "pages_read": pages_read,
        "count": min(len(leads), max_results),
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
    return {
        "company_id": match.group(1),
        "name": hit.get("companyName"),
        "industry": hit.get("industry"),
        "employees": hit.get("employeeCountRange") or hit.get("employeeDisplayCount"),
        "location": hit.get("location") or hit.get("headquarters"),
    }


async def find_company(page: Any, name: str) -> dict[str, Any]:
    """Resolve a company name with Sales Navigator's account search."""
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        await page.goto(account_search_url(name), wait_until="domcontentloaded")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESPONSE_TIMEOUT
        payload = None
        while loop.time() < deadline:
            await recorder.drain()
            if found := recorder.matching(ACCOUNT_SEARCH_PATH):
                payload = found[-1]["json"]
                break
            _raise_if_throttled()
            await asyncio.sleep(0.5)
    finally:
        recorder.remove()
        await recorder.drain()
    if payload is None:
        seen = sorted({r["url"].split("?")[0] for r in recorder.records})
        raise RuntimeError(
            f"Sales Navigator account search loaded no results data for '{name}'. "
            f"Responses seen: {seen[-8:]}"
        )
    accounts = [
        a
        for a in (account_from_hit(h) for h in payload.get("elements") or [])
        if a is not None
    ]
    if not accounts:
        raise ValueError(f"Sales Navigator found no company named '{name}'.")
    wanted = name.casefold().strip()
    exact = [a for a in accounts if (a["name"] or "").casefold().strip() == wanted]
    chosen = (exact or accounts)[0]
    return {"chosen": chosen, "exact_match": bool(exact), "candidates": accounts[:5]}
