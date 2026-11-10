"""Send a Sales Navigator message (InMail) from a lead page.

Selectors are attributes Sales Navigator puts on its own controls, not
translated text: ``data-anchor-send-inmail`` opens the composer,
``data-x-conversation-widget=compose-form`` is the form, and its
``data-sales-action`` button sends. Without ``confirm_send`` the message is
typed, read back and cleared again, so nothing leaves the browser.
"""

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.throttle import throttle_evidence, throttled_count

from dpi_salesnav.capture import SalesApiRecorder

logger = logging.getLogger(__name__)

OPEN_COMPOSER = "button[data-anchor-send-inmail]"
DIALOG = "section[role=dialog][data-message-overlay-container]"
FORM = "form[data-x-conversation-widget=compose-form]"
SUBJECT = f"{FORM} [id^=compose-form-subject-]"
BODY = f"{FORM} textarea[name=message]"
SEND = f"{FORM} button[data-sales-action]"
CLOSE = 'button[data-control-name="overlay.close_overlay"]'

SUBJECT_MAX = 200
BODY_MAX = 1900
_CREDITS_PATH = "/sales-api/salesApiCredits"
_SEND_TIMEOUT = 15.0
_LEAD_URL = re.compile(r"^/sales/lead/[^/]+,[^/]+,[^/]+/?$")


def _normalized(text: str) -> str:
    return " ".join(text.split())


class SendRefused(Exception):
    """The composer was not in a state where sending is safe."""


def validate_lead_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.netloc != "www.linkedin.com" or not _LEAD_URL.match(parsed.path):
        raise ValueError(
            "lead must be a Sales Navigator lead URL, "
            "https://www.linkedin.com/sales/lead/<id>,<authType>,<token> "
            "(sales_nav_url from salesnav_company_people)"
        )
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


def _credits(recorder: SalesApiRecorder) -> int | None:
    for record in reversed(recorder.matching(_CREDITS_PATH)):
        for grant in (record["json"] or {}).get("elements") or []:
            if isinstance(grant, dict) and "value" in grant:
                return grant["value"]
    return None


def _raise_if_throttled() -> None:
    if throttled_count():
        raise RateLimitError(
            throttle_evidence() or "LinkedIn throttled Sales Navigator."
        )


async def _discard(page: Any, has_subject: bool) -> None:
    """Empty the composer and close it, so no typed text is left behind."""
    try:
        await page.locator(BODY).fill("")
        if has_subject:
            await page.locator(SUBJECT).fill("")
        await page.locator(CLOSE).first.click()
    except Exception:
        logger.warning("Could not empty and close the InMail composer", exc_info=True)


async def _describe_buttons(send: Any) -> list[dict[str, Any]]:
    """What the send-button selector matched, by attributes only."""
    try:
        return await send.evaluate_all(
            """els => els.map(e => ({
                action: e.getAttribute('data-sales-action'),
                disabled: e.disabled || e.getAttribute('aria-disabled') === 'true',
                type: e.getAttribute('type'),
            }))"""
        )
    except Exception:
        return []


async def _human_pause(low: float = 0.8, high: float = 2.0) -> None:
    await asyncio.sleep(random.uniform(low, high))


async def send_inmail(
    page: Any,
    lead_url: str,
    subject: str,
    body: str,
    *,
    confirm_send: bool,
) -> dict[str, Any]:
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        await page.goto(lead_url, wait_until="domcontentloaded")
        opener = page.locator(OPEN_COMPOSER).first
        try:
            await opener.wait_for(state="visible", timeout=20_000)
        except Exception as e:
            _raise_if_throttled()
            raise SendRefused(
                "This lead page has no Sales Navigator message button "
                f"(page ended at {page.url})."
            ) from e
        await _human_pause()
        await opener.click()
        await page.locator(BODY).wait_for(state="visible", timeout=15_000)
        _raise_if_throttled()

        recipient = await page.locator(DIALOG).first.get_attribute("aria-label")
        has_subject = await page.locator(SUBJECT).count() > 0
        if has_subject:
            await page.locator(SUBJECT).fill(subject)
            await _human_pause(0.5, 1.2)
        await page.locator(BODY).fill(body)
        await _human_pause()

        typed_body = await page.locator(BODY).input_value()
        typed_subject = (
            await page.locator(SUBJECT).input_value() if has_subject else None
        )
        if typed_body != body or (has_subject and typed_subject != subject):
            await _discard(page, has_subject)
            raise SendRefused("The composer did not keep the text that was typed.")
        send = page.locator(SEND)
        if await send.count() != 1:
            buttons = await _describe_buttons(send)
            await recorder.drain()
            credits = _credits(recorder)
            await _discard(page, has_subject)
            raise SendRefused(
                "Could not identify exactly one send button; nothing was sent and "
                f"the composer was emptied. Buttons found: {buttons}. "
                f"InMail credits reported: {credits}."
            )
        send_enabled = await send.is_enabled()

        await recorder.drain()
        result: dict[str, Any] = {
            "lead_url": lead_url,
            "recipient": recipient,
            "kind": "inmail" if has_subject else "message",
            "subject": typed_subject,
            "body_chars": len(typed_body),
            "inmail_credits": _credits(recorder),
        }

        if not confirm_send:
            await page.locator(BODY).fill("")
            if has_subject:
                await page.locator(SUBJECT).fill("")
            await page.locator(CLOSE).first.click()
            result.update(
                status="preview",
                send_button_enabled=send_enabled,
                message="Nothing was sent. Call again with confirm_send=true to send.",
            )
            return result

        if not send_enabled:
            await _discard(page, has_subject)
            raise SendRefused(
                "The send button stayed disabled after typing; nothing was sent "
                f"(InMail credits reported: {result['inmail_credits']})."
            )

        recorder.records.clear()
        await send.click()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SEND_TIMEOUT
        posts: list[dict[str, Any]] = []
        while loop.time() < deadline:
            await recorder.drain()
            _raise_if_throttled()
            posts = [r for r in recorder.records if r["method"] == "POST"]
            cleared = (await page.locator(BODY).input_value()) == ""
            # The thread in the open conversation shows the message once Sales
            # Navigator has accepted it; a cleared box alone is not proof.
            thread = await page.locator(DIALOG).first.inner_text()
            if cleared and _normalized(body) in _normalized(thread):
                break
            await asyncio.sleep(0.5)
        else:
            result.update(
                status="unconfirmed",
                post_responses=[
                    {"status": r["status"], "path": urlparse(r["url"]).path}
                    for r in posts
                ],
                message=(
                    "Send was clicked but Sales Navigator did not confirm it. "
                    "Check the Sales Navigator inbox before retrying."
                ),
            )
            return result

        result.update(
            status="sent",
            post_responses=[
                {"status": r["status"], "path": urlparse(r["url"]).path} for r in posts
            ],
        )
        return result
    finally:
        recorder.remove()
        await recorder.drain()
