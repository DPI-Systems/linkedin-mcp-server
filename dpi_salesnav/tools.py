"""Sales Navigator MCP tools."""

import asyncio
import logging
import random
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.dependencies import get_ready_extractor, tool_errors
from linkedin_mcp_server.drivers.browser import get_or_create_browser

from dpi_salesnav.inmail import (
    BODY_MAX,
    SUBJECT_MAX,
    send_inmail,
    validate_lead_url,
)
from dpi_salesnav.people import company_people, find_company

logger = logging.getLogger(__name__)


async def _page(ctx: Context, tool_name: str) -> Any:
    await get_ready_extractor(ctx, tool_name=tool_name)
    return (await get_or_create_browser()).page


def register_salesnav_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register the Sales Navigator tools."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: People at Company",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"salesnav", "company"},
    )
    @tool_errors
    async def salesnav_company_people(
        company: str,
        ctx: Context,
        keywords: str | None = None,
        max_results: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """
        List the people currently working at a company, using Sales Navigator.

        Args:
            company: Company name as people write it (e.g. "DPI Systems",
                "CaixaBank") or numeric company id (e.g. "7848"). A name costs
                one extra page load (Sales Navigator account search); the
                chosen company and the other candidates are returned, so check
                company_exact_match. Pass the id when you have it.
            ctx: FastMCP context for progress reporting
            keywords: Optional Sales Navigator keywords to narrow the people,
                e.g. "director operaciones" or "CTO".
            max_results: How many people to return (1-100). Sales Navigator
                pages hold 25, and each page is one page load.

        Returns:
            Dict with company_id, total_available (Sales Navigator's count),
            pages_read and leads. Each lead has full_name, title, company,
            tenure_at_company and tenure_at_position ({years, months}),
            position_started_on, location, seniority, degree,
            recent_activity (e.g. "2 Publicaciones recientes en LinkedIn", or
            null), badges, summary, open_link, sales_profile_id and
            sales_nav_url (pass it to salesnav_send_inmail).
        """
        page = await _page(ctx, "salesnav_company_people")
        company = company.strip()
        resolved = None
        if company.isdigit():
            company_id = company
        else:
            resolved = await find_company(page, company)
            company_id = resolved["chosen"]["company_id"]
            await asyncio.sleep(random.uniform(2.0, 5.0))
        logger.info("Sales Navigator people at company %s", company_id)
        result = await company_people(
            page, company_id, keywords=keywords, max_results=max_results
        )
        if resolved:
            result["company"] = resolved["chosen"]
            result["company_exact_match"] = resolved["exact_match"]
            result["company_candidates"] = resolved["candidates"]
        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: Send InMail",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"salesnav", "actions"},
    )
    @tool_errors
    async def salesnav_send_inmail(
        lead_url: str,
        subject: Annotated[str, Field(min_length=1, max_length=SUBJECT_MAX)],
        body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)],
        ctx: Context,
        confirm_send: bool = False,
    ) -> dict[str, Any]:
        """
        Send a Sales Navigator message (InMail) to a lead.

        Without confirm_send this is a dry run: it opens the lead's composer,
        types subject and body, checks the send button, clears the text and
        closes the composer. Nothing is sent. Only pass confirm_send=true
        after a person has approved this exact recipient and text.

        Args:
            lead_url: sales_nav_url of the lead, from salesnav_company_people
                (https://www.linkedin.com/sales/lead/<id>,<authType>,<token>).
            subject: InMail subject. Ignored when Sales Navigator shows no
                subject field (e.g. 1st-degree connections).
            body: Message text.
            ctx: FastMCP context for progress reporting
            confirm_send: Actually send. Default false (preview only).

        Returns:
            Dict with status ("preview", "sent" or "unconfirmed"), recipient,
            kind ("inmail" or "message"), inmail_credits when the page
            reported them, and the account's sent_today against daily_cap.
            "unconfirmed" means send was clicked but not confirmed: check the
            Sales Navigator inbox and do not retry blindly.
        """
        url = validate_lead_url(lead_url)
        page = await _page(ctx, "salesnav_send_inmail")
        logger.info("Sales Navigator InMail to %s (send=%s)", url, confirm_send)
        return await send_inmail(page, url, subject, body, confirm_send=confirm_send)
