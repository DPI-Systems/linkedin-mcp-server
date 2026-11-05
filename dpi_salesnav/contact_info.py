"""A connection's contact details, from the profile's "Contact info" dialog.

LinkedIn no longer opens that dialog from its own URL: ``/overlay/contact-info/``
now lands on the plain profile, which is why upstream's ``contact_info``
section finds no overlay. The dialog opens only from the link on the profile,
so this loads the profile and clicks that link (found by its URL, not its
words).

What is read is locale-independent: emails are ``mailto:`` links, websites are
LinkedIn's ``/safety/go/?url=`` redirects, and phones are the lines that are a
phone number. The dialog's full text comes back too, for anything else on it
(birthday, connected since), in the account's language.
"""

import asyncio
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from linkedin_mcp_server.error_handler import CodedToolError, ErrorCode

from dpi_salesnav.people import _raise_if_throttled

logger = logging.getLogger(__name__)

PROFILE_URL = "https://www.linkedin.com/in/{}/"
_LINK = 'main a[href*="/overlay/contact-info/"]'
_DIALOG = "dialog[open]"
_NAV_TIMEOUT = 30_000
_USERNAME = re.compile(r"^[^/?#\s]{2,200}$")
# "634 93 87 86 (móvil)", "+34 954 000 000", "(+1) 415-555-0172 (work)"
_PHONE = re.compile(
    r"^(?P<number>\(?\+?\d[\d\s().-]{5,}\d)\s*(?:\((?P<kind>[^)]*)\))?$"
)

_READ = r"""(dialog) => ({
  text: dialog.innerText,
  hrefs: [...dialog.querySelectorAll('a[href]')].map(a => a.getAttribute('href')),
})"""


class ContactInfoUnavailable(CodedToolError):
    """The profile shows no contact-info link (not a connection, or hidden)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.NOT_FOUND)


def validate_username(username: str) -> str:
    username = unquote(username.strip().strip("/"))
    if username.startswith("in/"):
        username = username[3:]
    if not _USERNAME.match(username):
        raise ValueError(f"Not a LinkedIn username: {username!r}")
    return username


def _website(href: str) -> str | None:
    parsed = urlparse(href)
    if parsed.path.startswith("/safety/go"):
        target = parse_qs(parsed.query).get("url", [""])[0]
        return target or None
    if parsed.scheme in ("http", "https") and "linkedin.com" not in parsed.netloc:
        return href
    return None


def contact_from_dialog(text: str, hrefs: list[str]) -> dict[str, Any]:
    emails = list(
        dict.fromkeys(h[7:].split("?")[0] for h in hrefs if h.startswith("mailto:"))
    )
    websites = list(dict.fromkeys(w for h in hrefs if (w := _website(h))))
    phones = []
    for line in (s.strip() for s in text.split("\n")):
        m = _PHONE.match(line)
        if m and sum(c.isdigit() for c in m.group("number")) >= 6:
            phones.append(
                {"number": m.group("number").strip(), "kind": m.group("kind")}
            )
    return {
        "emails": emails,
        "phones": phones,
        "websites": websites,
        "text": text.strip(),
    }


async def read_contact_info(page: Any, username: str) -> dict[str, Any]:
    username = validate_username(username)
    url = PROFILE_URL.format(username)
    await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    _raise_if_throttled()
    link = page.locator(_LINK).first
    try:
        await link.wait_for(state="visible", timeout=15_000)
    except Exception as exc:
        raise ContactInfoUnavailable(
            f"{url} shows no contact-info link; LinkedIn shows contact details "
            "to connections only."
        ) from exc
    await asyncio.sleep(0.8)
    await link.click()
    dialog = page.locator(_DIALOG).first
    await dialog.wait_for(state="visible", timeout=15_000)
    # The dialog fills in after it opens; wait for its text to settle.
    previous = ""
    for _ in range(10):
        await asyncio.sleep(0.5)
        current = await dialog.inner_text()
        if current and current == previous:
            break
        previous = current
    data = await dialog.evaluate(_READ)
    await page.keyboard.press("Escape")
    _raise_if_throttled()
    return {
        "url": url,
        "linkedin_username": username,
        **contact_from_dialog(data["text"], data["hrefs"]),
    }
