"""Send a Sales Navigator message (InMail) from a lead page.

Selectors are attributes Sales Navigator puts on its own controls, not
translated text: ``data-anchor-send-inmail`` opens the composer,
``data-x-conversation-widget=compose-form`` is the form, and its
``data-sales-action`` button sends. Without ``confirm_send`` the message is
typed, read back and cleared again, so nothing leaves the browser.
"""

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import random
import re
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.config import get_config
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

DAILY_CAP = int(os.environ.get("DPI_SALESNAV_INMAIL_DAILY_CAP", "15"))
_STATE_DIR = pathlib.Path(
    os.environ.get("DPI_SALESNAV_STATE_DIR", "~/.dpi-salesnav")
).expanduser()


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


# Local send log, one per browser profile, kept outside the profile tree
# because rotating a session deletes the profile's parent directory.
def _log_path() -> pathlib.Path:
    profile = str(pathlib.Path(get_config().browser.user_data_dir).expanduser())
    key = hashlib.sha256(profile.encode()).hexdigest()[:12]
    return _STATE_DIR / f"inmail-{key}.jsonl"


def sent_today() -> int:
    path = _log_path()
    if not path.exists():
        return 0
    today = dt.date.today().isoformat()
    return sum(
        1
        for line in path.read_text().splitlines()
        if line.startswith(f'{{"date": "{today}"')
    )


def _log_send(entry: dict[str, Any]) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"date": dt.date.today().isoformat(), **entry}
    with path.open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


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
    if confirm_send and sent_today() >= DAILY_CAP:
        raise SendRefused(
            f"Daily cap reached: {DAILY_CAP} messages sent today from this "
            "account (DPI_SALESNAV_INMAIL_DAILY_CAP)."
        )

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
            raise SendRefused("The composer did not keep the text that was typed.")
        send = page.locator(SEND)
        if await send.count() != 1:
            raise SendRefused("Could not identify exactly one send button.")
        send_enabled = await send.is_enabled()

        await recorder.drain()
        result: dict[str, Any] = {
            "lead_url": lead_url,
            "recipient": recipient,
            "kind": "inmail" if has_subject else "message",
            "subject": typed_subject,
            "body_chars": len(typed_body),
            "inmail_credits": _credits(recorder),
            "sent_today_before": sent_today(),
            "daily_cap": DAILY_CAP,
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
            raise SendRefused("The send button stayed disabled after typing.")

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
            _log_send({"lead_url": lead_url, "status": "unconfirmed"})
            return result

        _log_send({"lead_url": lead_url, "status": "sent", "subject": subject})
        result.update(
            status="sent",
            sent_today=sent_today(),
            post_responses=[
                {"status": r["status"], "path": urlparse(r["url"]).path} for r in posts
            ],
        )
        return result
    finally:
        recorder.remove()
        await recorder.drain()
