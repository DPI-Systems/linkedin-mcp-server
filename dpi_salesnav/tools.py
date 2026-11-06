"""Sales Navigator MCP tools."""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.dependencies import get_ready_extractor, tool_errors
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools.person import StrList

from dpi_salesnav.inmail import (
    BODY_MAX,
    SUBJECT_MAX,
    send_inmail,
    validate_lead_url,
)
from dpi_salesnav.article import TITLE_MAX, create_article, publish_article
from dpi_salesnav.celebrations import read_celebrations
from dpi_salesnav.contact_info import read_contact_info
from dpi_salesnav.inbox import read_inbox, read_thread
from dpi_salesnav.people import LeadFilters, company_people, search_companies
from dpi_salesnav.verification import (
    finish_verification,
    open_verification,
    verification_status,
)

logger = logging.getLogger(__name__)


async def _page(ctx: Context, tool_name: str) -> Any:
    await get_ready_extractor(ctx, tool_name=tool_name)
    return (await get_or_create_browser()).page


def _filters(
    keywords: str | None,
    titles: list[str] | None,
    seniority: list[str] | None,
    functions: list[str] | None,
    regions: list[str] | None,
    posted_recently: bool,
    changed_jobs_recently: bool,
) -> LeadFilters:
    return LeadFilters(
        keywords=keywords,
        titles=tuple(titles or ()),
        seniority=tuple(seniority or ()),
        functions=tuple(functions or ()),
        regions=tuple(regions or ()),
        posted_recently=posted_recently,
        changed_jobs_recently=changed_jobs_recently,
    )


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
        company_id: str,
        ctx: Context,
        keywords: str | None = None,
        titles: StrList | None = None,
        seniority: StrList | None = None,
        functions: StrList | None = None,
        regions: StrList | None = None,
        posted_recently: bool = False,
        changed_jobs_recently: bool = False,
        max_results: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """
        List the people currently working at a company, using Sales Navigator.

        Args:
            company_id: Numeric Sales Navigator company id (e.g. "7848"),
                from salesnav_search_companies.
            ctx: FastMCP context for progress reporting
            keywords: Optional Sales Navigator keywords to narrow the people,
                e.g. "licitaciones" or "contratación pública".
            titles: Current job titles to include (free text, any of them),
                e.g. ["Responsable de licitaciones", "Gerente"].
            seniority: Any of training, entry, senior, strategic,
                entry_manager, experienced_manager, director, vp, cxo, owner.
            functions: Any of accounting, administrative, arts_design,
                business_development, community_social, consulting,
                education, engineering, entrepreneurship, finance,
                healthcare, human_resources, it, legal, marketing,
                media_communication, military_protective, operations,
                product_management, program_project_management, purchasing,
                quality_assurance, real_estate, research, sales,
                customer_success.
            regions: Numeric LinkedIn geo ids, e.g. ["105646813"] for Spain.
            posted_recently: Only people who posted on LinkedIn in the last
                30 days. Sales Navigator applies it even when it leaves a
                lead's recent_activity badge out, so recent_activity may be
                null on a match.
            changed_jobs_recently: Only people who changed jobs or were
                promoted in the last 90 days.
            max_results: How many people to return (1-100). Sales Navigator
                pages hold 25, and each page is one page load.

        Returns:
            Dict with company_id, total_available (Sales Navigator's count),
            pages_read, region_breakdown and leads. Each lead has full_name, title, company,
            tenure_at_company and tenure_at_position ({years, months}),
            position_started_on, location, seniority, degree,
            recent_activity (e.g. "2 Publicaciones recientes en LinkedIn", or
            null), changed_jobs_recently, promoted_recently, badges, summary, open_link, sales_profile_id and
            sales_nav_url (pass it to salesnav_send_inmail).
        """
        filters = _filters(
            keywords,
            titles,
            seniority,
            functions,
            regions,
            posted_recently,
            changed_jobs_recently,
        )
        company_id = company_id.strip()
        if not company_id.isdigit():
            raise ValueError(
                "company_id must be the numeric Sales Navigator company id; "
                "find it with salesnav_search_companies."
            )
        page = await _page(ctx, "salesnav_company_people")
        logger.info("Sales Navigator people at company %s", company_id)
        return await company_people(
            page, company_id, filters=filters, max_results=max_results
        )

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: Search Companies",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"salesnav", "company", "search"},
    )
    @tool_errors
    async def salesnav_search_companies(name: str, ctx: Context) -> dict[str, Any]:
        """
        Find companies by name with Sales Navigator account search.

        Returns every match Sales Navigator gives (up to 25), not a guess:
        compare name, industry, employees and location to pick the right
        one, then pass its company_id to salesnav_company_people. Joint
        ventures (UTE), subsidiaries and similarly named companies show up
        here too.

        Args:
            name: Company name as people write it, e.g. "CaixaBank" or
                "DPI Systems". Shorter names match more companies.
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with query, total_available, count and companies. Each
            company has company_id, name, industry, employees, location,
            description, sales_nav_url and linkedin_url.
        """
        if not name.strip():
            raise ValueError("name must not be empty")
        page = await _page(ctx, "salesnav_search_companies")
        return await search_companies(page, name.strip())

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: Search People",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"salesnav", "search"},
    )
    @tool_errors
    async def salesnav_search_people(
        ctx: Context,
        keywords: str | None = None,
        titles: StrList | None = None,
        seniority: StrList | None = None,
        functions: StrList | None = None,
        regions: StrList | None = None,
        posted_recently: bool = False,
        changed_jobs_recently: bool = False,
        max_results: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """
        Search people across all companies with Sales Navigator lead search.

        Same filters and lead fields as salesnav_company_people, without the
        company. At least one filter is required.

        To filter by a place without knowing its geo id: search with the place
        name in keywords (e.g. "LangChain Chennai"), read region_breakdown,
        then search again with that region's id in regions and the place
        name taken out of keywords.

        Returns:
            Dict with total_available, pages_read, region_breakdown (id, name,
            count of the results in each region) and leads.
        """
        filters = _filters(
            keywords,
            titles,
            seniority,
            functions,
            regions,
            posted_recently,
            changed_jobs_recently,
        )
        if filters == LeadFilters():
            raise ValueError("Give at least one filter or keywords.")
        page = await _page(ctx, "salesnav_search_people")
        result = await company_people(
            page, None, filters=filters, max_results=max_results
        )
        result.pop("company_id")
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
            reported them.
            "unconfirmed" means send was clicked but not confirmed: check the
            Sales Navigator inbox and do not retry blindly.
        """
        url = validate_lead_url(lead_url)
        page = await _page(ctx, "salesnav_send_inmail")
        logger.info("Sales Navigator InMail to %s (send=%s)", url, confirm_send)
        return await send_inmail(page, url, subject, body, confirm_send=confirm_send)

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: Inbox",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        },
        tags={"salesnav", "messaging"},
    )
    @tool_errors
    async def salesnav_inbox(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=20)] = 20,
    ) -> dict[str, Any]:
        """
        List the newest Sales Navigator inbox threads (up to 20).

        Opening the inbox also opens its newest thread, which Sales Navigator
        then marks as read.

        Args:
            ctx: FastMCP context for progress reporting
            limit: How many threads to return (1-20).

        Returns:
            Dict with threads. Each thread has thread_id, contact (full_name,
            degree, member_urn, sales_nav_url), unread_count, total_messages,
            last_message (type, from_me, subject, body, delivered_at),
            awaiting_their_reply (the last message is ours) and they_replied
            (judged from the newest message only; use salesnav_get_thread
            for the whole history).
        """
        page = await _page(ctx, "salesnav_inbox")
        return await read_inbox(page, limit=limit)

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Contact Info",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"network"},
    )
    @tool_errors
    async def linkedin_contact_info(
        linkedin_username: str, ctx: Context
    ) -> dict[str, Any]:
        """
        Read a connection's contact details: emails, phones and websites.

        Opens the profile and its "Contact info" dialog, which LinkedIn shows
        to connections only, and only with what each person chose to share.
        Use this instead of get_person_profile's contact_info section, which
        no longer finds the dialog.

        Args:
            linkedin_username: Profile username (the part after /in/).
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, linkedin_username, emails, phones (number, kind as
            LinkedIn labels it, e.g. "móvil"), websites, and text: the whole
            dialog as rendered, which also holds the birthday and the date you
            connected when the person shares them.
        """
        page = await _page(ctx, "linkedin_contact_info")
        return await read_contact_info(page, linkedin_username)

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Network Updates (birthdays, job changes)",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"network"},
    )
    @tool_errors
    async def linkedin_network_updates(
        ctx: Context,
        categories: StrList | None = None,
        max_results: Annotated[int, Field(ge=1, le=50)] = 30,
    ) -> dict[str, Any]:
        """
        Read the "Catch up" updates of your connections: birthdays, job
        changes, work anniversaries and education milestones.

        Use it to find a reason to write to someone. To congratulate a
        connection, pass its linkedin_username to send_message; to comment on
        a job change or anniversary, the post_url is the announcement.

        Args:
            ctx: FastMCP context for progress reporting
            categories: Any of "birthdays", "job_changes",
                "work_anniversaries", "education". Default: all four, one page
                load each.
            max_results: Most entries returned per category (1-50).

        Returns:
            Dict with categories: {category: [entries]}. Each entry has name,
            detail (LinkedIn's own line in the account's language, e.g. when
            the birthday is or was, or the new position and company),
            linkedin_username, profile_url and post_url (null for birthdays).
            LinkedIn lists birthdays from the last days up to today only, and
            only of people who share them. errors names a category that could
            not be read.
        """
        page = await _page(ctx, "linkedin_network_updates")
        return await read_celebrations(page, categories, max_results=max_results)

    @mcp.tool(
        timeout=tool_timeout,
        title="Sales Navigator: Get Thread",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        },
        tags={"salesnav", "messaging"},
    )
    @tool_errors
    async def salesnav_get_thread(thread_id: str, ctx: Context) -> dict[str, Any]:
        """
        Read every message of one Sales Navigator thread, oldest first.

        Opening the thread marks it as read.

        Args:
            thread_id: thread_id from salesnav_inbox (starts with "2-").
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with thread_id, contact, unread_count, messages (each with
            type, from_me, subject, body, delivered_at, attachments),
            awaiting_their_reply and they_replied.
        """
        page = await _page(ctx, "salesnav_get_thread")
        return await read_thread(page, thread_id)

    @mcp.tool(
        timeout=max(tool_timeout, 600),
        title="LinkedIn: Create Article",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"article", "actions"},
    )
    @tool_errors
    async def salesnav_create_article(
        title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)],
        markdown: str,
        ctx: Context,
        images_dir: str | None = None,
        cover_image: str | None = None,
        author: str | None = None,
        publish_in: str | None = None,
        publish: bool = False,
        commentary: str | None = None,
    ) -> dict[str, Any]:
        """
        Write a LinkedIn article (linkedin.com/pulse/...) from Markdown.

        By default this only saves a draft and returns its URL, so a person
        can review it in LinkedIn; publish the reviewed draft with
        salesnav_publish_article. publish=true writes and publishes in one go,
        only for text a person has already approved.

        Markdown supported: # and ## (large heading), ### and deeper (small
        heading), paragraphs, **bold**, *italic*, [links](https://...),
        bullet and numbered lists, > quotes, ``` code blocks, --- dividers,
        and images on their own line as ![caption](file.png). Tables, raw
        HTML and strikethrough are not supported and come back in warnings.

        Args:
            title: Article title (up to 150 characters).
            markdown: Article body in Markdown.
            ctx: FastMCP context for progress reporting
            images_dir: Folder the Markdown image paths are relative to.
                Images must be local PNG, JPG, GIF or WebP files (8 MB max).
            cover_image: Local path of the cover image (1280x720 works well).
            author: "Publish as": the member's own name (default) or the
                exact name of a LinkedIn page the member administers.
            publish_in: Newsletter name to publish as an edition of it, which
                LinkedIn sends to its subscribers. Default (null) is a
                standalone article.
            publish: Actually publish. Default false (draft only).
            commentary: Text of the feed post that announces the article
                when publishing.

        Returns:
            Dict with status ("draft", "published" or "unconfirmed"),
            draft_id, draft_url, author, published_in, cover, saved (what
            the reopened draft holds: images_uploaded, headings, lists,
            quotes, code blocks, links, characters), warnings and, when
            published, url.
        """
        page = await _page(ctx, "salesnav_create_article")
        return await create_article(
            page,
            title=title,
            markdown=markdown,
            images_dir=images_dir,
            cover_image=cover_image,
            author=author,
            publish_in=publish_in,
            publish=publish,
            commentary=commentary,
        )

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Publish Article Draft",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"article", "actions"},
    )
    @tool_errors
    async def salesnav_publish_article(
        draft_id: str,
        ctx: Context,
        commentary: str | None = None,
        confirm_publish: bool = False,
    ) -> dict[str, Any]:
        """
        Publish an article draft made by salesnav_create_article.

        Without confirm_publish it only reports the draft's title, author and
        destination (standalone article or a newsletter, which LinkedIn mails
        to its subscribers). Pass confirm_publish=true once those are approved.

        Args:
            draft_id: draft_id from salesnav_create_article.
            ctx: FastMCP context for progress reporting
            commentary: Text of the feed post announcing the article.
            confirm_publish: Actually publish. Default false.

        Returns:
            Dict with draft_id, draft_url, title, author, published_in, saved
            and status ("not_published", "published" or "unconfirmed"; for
            "unconfirmed", check LinkedIn before trying again).
        """
        page = await _page(ctx, "salesnav_publish_article")
        return await publish_article(
            page, draft_id, commentary=commentary, confirm_publish=confirm_publish
        )

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Open Verification Viewer",
        annotations={"readOnlyHint": False, "openWorldHint": True},
        tags={"session"},
    )
    @tool_errors
    async def linkedin_verification_open() -> dict[str, Any]:
        """
        Let a person solve a LinkedIn checkpoint in this server's own browser.

        Call it when a tool fails with account_restricted or auth_required, or
        LinkedIn shows a captcha, a code request or a verification page. It
        opens the browser on the stored session (without the session check
        that fails on a checkpoint) and returns viewer_url, a noVNC page
        showing that browser. Give the URL to the user and wait for them: do
        not solve the check yourself and do not call other LinkedIn tools
        meanwhile. Then call linkedin_verification_status, and
        linkedin_verification_finish once they are done. The viewer closes on
        its own after 30 minutes.

        Returns:
            Dict with viewer_url, expires_in_seconds, page_url, state
            ("restricted", "challenge", "ok", "off_linkedin") and already_open.
        """
        return await open_verification()

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Verification Status",
        annotations={"readOnlyHint": True, "openWorldHint": False},
        tags={"session"},
    )
    @tool_errors
    async def linkedin_verification_status(
        wait_seconds: Annotated[int, Field(ge=0, le=600)] = 0,
    ) -> dict[str, Any]:
        """
        Where the verification browser is now, read from addresses only.

        Never navigates or reloads, so waiting here costs LinkedIn nothing.
        state "challenge" or "restricted" means the check is still there
        (a sign-in or checkpoint route, the restriction route, or a captcha
        frame inside the page); "ok" means none of those is left, so the
        person is probably done: confirm with them, then call
        linkedin_verification_finish.

        Args:
            wait_seconds: Wait up to this long for the check to clear,
                checking every 2 s (capped below the tool timeout).

        Returns:
            Dict with open, state, page_url, waited_seconds and, while open,
            viewer_url and expires_in_seconds.
        """
        return await verification_status(min(wait_seconds, max(0, tool_timeout - 20)))

    @mcp.tool(
        timeout=tool_timeout,
        title="LinkedIn: Finish Verification",
        annotations={"readOnlyHint": False, "openWorldHint": False},
        tags={"session"},
    )
    @tool_errors
    async def linkedin_verification_finish() -> dict[str, Any]:
        """
        Close the verification viewer and the browser behind it.

        The browser saves its cookies on the way out and the next LinkedIn
        tool reopens it and checks the session as usual, so the work can go
        on. If state is still "restricted" or "challenge", the check was not
        cleared: tell the user instead of carrying on.

        Returns:
            Dict with state and page_url as they were before closing, closed
            (whether a viewer was open) and reason.
        """
        return await finish_verification()
