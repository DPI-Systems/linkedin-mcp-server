"""Send a Sales Navigator message (InMail) from a lead page.

Selectors are attributes Sales Navigator puts on its own controls, not
translated text: ``data-anchor-send-inmail`` opens the composer,
``data-x-conversation-widget=compose-form`` is the form, and its
``data-sales-action`` button sends. Without ``confirm_send`` the message is
typed, read back and cleared again, so nothing leaves the browser.

Attachments go through the composer's own ``input[type=file]``. Sales
Navigator shows each one by its file name, which nobody translates, so the
name (in text or in any attribute) is what proves a file is attached, what
finds its remove button, and what the thread must show after sending.
"""

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.throttle import throttle_evidence, throttled_count

from dpi_salesnav.attachments import Attachment
from dpi_salesnav.capture import SalesApiRecorder

logger = logging.getLogger(__name__)

OPEN_COMPOSER = "button[data-anchor-send-inmail]"
DIALOG = "section[role=dialog][data-message-overlay-container]"
FORM = "form[data-x-conversation-widget=compose-form]"
SUBJECT = f"{FORM} [id^=compose-form-subject-]"
BODY = f"{FORM} textarea[name=message]"
# Sales Navigator puts data-sales-action on other composer buttons too (copy,
# the "Message IQ" draft helper, measured on an English-language account); the
# send button is the one that carries aria-disabled, present in every state.
SEND = f"{FORM} button[data-sales-action][aria-disabled]"
CLOSE = 'button[data-control-name="overlay.close_overlay"]'
FILE_INPUT = f"{DIALOG} input[type=file]"
REMOVE_MARK = "data-dpi-remove-attachment"

SUBJECT_MAX = 200
BODY_MAX = 1900
_CREDITS_PATH = "/sales-api/salesApiCredits"
_SEND_TIMEOUT = 15.0
# An 8 MB PDF over a home uplink; the thread may also take a while to list it.
_UPLOAD_TIMEOUT = 120.0
_REMOVE_TIMEOUT = 15.0
_THREAD_ATTACHMENT_TIMEOUT = 60.0
INMAIL_TOOL_TIMEOUT = 360.0
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


async def _discard(
    page: Any, has_subject: bool, names: list[str] | None = None
) -> list[str]:
    """Empty the composer and close it, so no typed text or file is left behind.

    Returns the attachment names that could not be removed.
    """
    left = await _remove_attachments(page, names) if names else []
    try:
        await page.locator(BODY).fill("")
        if has_subject:
            await page.locator(SUBJECT).fill("")
        await page.locator(CLOSE).first.click()
    except Exception:
        logger.warning("Could not empty and close the InMail composer", exc_info=True)
    return left


async def _describe_buttons(send: Any) -> list[dict[str, Any]]:
    """What the send-button selector matched, by attributes only."""
    try:
        return await send.evaluate_all(
            """els => els.map(e => ({
                action: e.getAttribute('data-sales-action'),
                disabled: e.disabled || e.getAttribute('aria-disabled') === 'true',
                type: e.getAttribute('type'),
                attrs: Object.fromEntries([...e.attributes]
                    .filter(a => a.name !== 'class' && a.name !== 'style')
                    .map(a => [a.name, a.value.slice(0, 80)])),
                parents: (() => { const out = []; let p = e.parentElement;
                    for (let i = 0; p && i < 4; i++, p = p.parentElement)
                        out.push(p.tagName.toLowerCase() + [...p.attributes]
                            .filter(a => a.name !== 'style')
                            .map(a => '[' + a.name + '=' + a.value.slice(0, 40) + ']').join(''));
                    return out; })(),
            }))"""
        )
    except Exception:
        return []


# Which names appear in root (text or any attribute value), leaving out the
# parts matched by `exclude` and every form field, whose value is our own text.
_NAMES_PRESENT = """(root, {names, exclude}) => {
    const skeleton = el => [...el.querySelectorAll('*')].slice(0, 120).map(e =>
        '  '.repeat(Math.min(depth(e, el), 12)) + e.tagName.toLowerCase() +
        [...e.attributes].filter(a => a.name !== 'style')
            .map(a => ' ' + a.name + '=' + a.value.slice(0, 40)).join('')).join('\\n');
    const depth = (e, top) => { let d = 0; while (e && e !== top) { e = e.parentElement; d++; } return d; };
    const clone = root.cloneNode(true);
    const drop = 'input, textarea, script, style' + (exclude ? ', ' + exclude : '');
    clone.querySelectorAll(drop).forEach(e => e.remove());
    const norm = s => (s || '').replace(/\\s+/g, ' ');
    const parts = [clone.textContent];
    for (const el of [clone, ...clone.querySelectorAll('*')]) {
        for (const a of el.attributes) parts.push(a.value);
    }
    const hay = norm(parts.join(' '));
    // A progress bar can stay in the DOM once the upload is done, so it only
    // counts while it is shown and short of its maximum (or indeterminate).
    const skip = exclude ? [...root.querySelectorAll(exclude)] : [];
    const busyBy = [...root.querySelectorAll(
        'progress, [role=progressbar], [aria-busy=true]'
    )].filter(e => !skip.some(x => x.contains(e))).map(e => {
        const shown = e.checkVisibility ? e.checkVisibility() : !!e.offsetParent;
        let done;
        if (e.tagName === 'PROGRESS') {
            done = e.position !== -1 && e.value >= e.max;
        } else if (e.hasAttribute('aria-valuenow')) {
            done = Number(e.getAttribute('aria-valuenow')) >=
                Number(e.getAttribute('aria-valuemax') || 100);
        } else {
            done = false;
        }
        return {tag: e.tagName.toLowerCase(), shown, done,
                value: e.tagName === 'PROGRESS' ? e.value : e.getAttribute('aria-valuenow')};
    }).filter(b => b.shown && !b.done);
    return {found: names.filter(n => hay.includes(norm(n))), busy: busyBy.length > 0,
            busy_by: busyBy, skeleton: skeleton(clone)};
}"""

# Mark the button that removes the attachment called `name`: start at the
# innermost element naming it and climb until a container holds exactly one
# button other than send, never as far as the one holding the message box.
_MARK_REMOVE = """(form, {name, mark}) => {
    const norm = s => (s || '').replace(/\\s+/g, ' ');
    const names = el => norm(el.textContent).includes(norm(name)) ||
        [...el.attributes].some(a => norm(a.value).includes(norm(name)));
    const hits = [...form.querySelectorAll('*')].filter(el =>
        !el.matches('input, textarea') && names(el));
    const innermost = hits.filter(el => !hits.some(o => o !== el && el.contains(o)));
    for (const start of innermost) {
        for (let el = start; el && el !== form; el = el.parentElement) {
            if (el.querySelector('textarea')) break;
            const buttons = [el, ...el.querySelectorAll('button')].filter(b =>
                b.matches('button') && !b.matches('[data-sales-action]'));
            if (buttons.length === 1) {
                form.querySelectorAll('[' + mark + ']').forEach(b => b.removeAttribute(mark));
                buttons[0].setAttribute(mark, '');
                return true;
            }
            if (buttons.length > 1) break;
        }
    }
    return false;
}"""

# Attributes of the dialog's controls (no text), for an error that has to be
# fixed against a changed Sales Navigator.
_DESCRIBE_CONTROLS = """root => [...root.querySelectorAll('button, input, label[for]')]
    .slice(0, 40).map(e => Object.fromEntries(
        [['tag', e.tagName.toLowerCase()],
         ...[...e.attributes].filter(a => a.name !== 'class' && a.name !== 'style')
            .map(a => [a.name, a.value.slice(0, 80)])]))"""


async def _names_present(
    page: Any, names: list[str], *, scope: str, exclude: str | None = None
) -> dict[str, Any]:
    root = page.locator(scope).first
    return await root.evaluate(_NAMES_PRESENT, {"names": names, "exclude": exclude})


async def _describe_controls(page: Any) -> list[dict[str, Any]]:
    try:
        return await page.locator(DIALOG).first.evaluate(_DESCRIBE_CONTROLS)
    except Exception:
        return []


async def _attach(page: Any, attachments: list[Attachment]) -> None:
    """Hand the files to the composer and wait until each one is shown."""
    inputs = page.locator(FILE_INPUT)
    if await inputs.count() != 1:
        raise SendRefused(
            "Could not identify exactly one file input in the composer, so "
            "nothing was attached or sent. Controls found: "
            f"{await _describe_controls(page)}"
        )
    names = [a.name for a in attachments]
    if await inputs.get_attribute("multiple") is None and len(attachments) > 1:
        for attachment in attachments:
            await inputs.set_input_files(str(attachment.path))
            await _human_pause(0.5, 1.2)
    else:
        await inputs.set_input_files([str(a.path) for a in attachments])

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _UPLOAD_TIMEOUT
    state: dict[str, Any] = {"found": [], "busy": True}
    while loop.time() < deadline:
        _raise_if_throttled()
        state = await _names_present(page, names, scope=FORM)
        if len(state["found"]) == len(names) and not state["busy"]:
            send = page.locator(SEND)
            if await send.count() == 1 and await send.is_enabled():
                return
        await asyncio.sleep(1.0)
    missing = sorted(set(names) - set(state["found"]))
    logger.warning("InMail composer after attaching:\n%s", state.get("skeleton"))
    raise SendRefused(
        "The attachment could not be verified in the composer "
        f"(not shown: {missing}, upload still busy: {state.get('busy_by')}); "
        "nothing was sent. Controls found: "
        f"{await _describe_controls(page)}"
    )


async def _remove_attachments(page: Any, names: list[str]) -> list[str]:
    """Remove each named attachment; return the names still in the composer."""
    form = page.locator(FORM).first
    for name in names:
        try:
            if not await form.evaluate(
                _MARK_REMOVE, {"name": name, "mark": REMOVE_MARK}
            ):
                continue
            await page.locator(f"{FORM} [{REMOVE_MARK}]").first.click()
            await _human_pause(0.4, 1.0)
        except Exception:
            logger.warning("Could not remove attachment %s", name, exc_info=True)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REMOVE_TIMEOUT
    left = names
    while loop.time() < deadline:
        try:
            left = (await _names_present(page, names, scope=FORM))["found"]
        except Exception:
            return names
        if not left:
            return []
        await asyncio.sleep(0.5)
    return left


async def _human_pause(low: float = 0.8, high: float = 2.0) -> None:
    await asyncio.sleep(random.uniform(low, high))


async def send_inmail(
    page: Any,
    lead_url: str,
    subject: str,
    body: str,
    *,
    confirm_send: bool,
    attachments: list[Attachment] | None = None,
) -> dict[str, Any]:
    attachments = attachments or []
    names = [a.name for a in attachments]
    recorder = SalesApiRecorder(page)
    recorder.install()
    # What is in the composer and has to be taken out again if anything stops
    # this call before send is clicked.
    pending: dict[str, Any] = {"open": False, "subject": False, "names": []}
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
        pending.update(open=True, subject=has_subject)
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
            buttons = await _describe_buttons(send)
            await recorder.drain()
            raise SendRefused(
                "Could not identify exactly one send button; nothing was sent and "
                f"the composer was emptied. Buttons found: {buttons}. "
                f"InMail credits reported: {_credits(recorder)}."
            )
        if attachments:
            pending["names"] = names
            await _attach(page, attachments)
        send_enabled = await send.is_enabled()

        await recorder.drain()
        result: dict[str, Any] = {
            "lead_url": lead_url,
            "recipient": recipient,
            "kind": "inmail" if has_subject else "message",
            "subject": typed_subject,
            "body_chars": len(typed_body),
            "inmail_credits": _credits(recorder),
            "attachments": [a.describe() for a in attachments],
        }

        if not confirm_send:
            left = await _discard(page, has_subject, names)
            pending["open"] = False
            if left:
                raise SendRefused(
                    "Dry run: the attachment was verified but could not be "
                    f"removed again ({left} still shown) before closing the "
                    "composer. Nothing was sent; check the composer for this "
                    "lead before the next message."
                )
            result.update(
                status="preview",
                send_button_enabled=send_enabled,
                attachments_verified=bool(attachments),
                attachments_removed=names,
                message="Nothing was sent. Call again with confirm_send=true to send.",
            )
            return result

        if not send_enabled:
            raise SendRefused(
                "The send button stayed disabled after typing; nothing was sent "
                f"(InMail credits reported: {result['inmail_credits']})."
            )

        recorder.records.clear()
        pending["open"] = False
        await send.click()
        loop = asyncio.get_running_loop()
        deadline = (
            loop.time() + _SEND_TIMEOUT + (_THREAD_ATTACHMENT_TIMEOUT if names else 0)
        )
        posts: list[dict[str, Any]] = []
        in_thread: list[str] = []
        text_seen = False
        while loop.time() < deadline:
            await recorder.drain()
            _raise_if_throttled()
            posts = [r for r in recorder.records if r["method"] == "POST"]
            cleared = (await page.locator(BODY).input_value()) == ""
            # The thread in the open conversation shows the message once Sales
            # Navigator has accepted it; a cleared box alone is not proof.
            thread = await page.locator(DIALOG).first.inner_text()
            text_seen = cleared and _normalized(body) in _normalized(thread)
            if names:
                in_thread = (
                    await _names_present(page, names, scope=DIALOG, exclude=FORM)
                )["found"]
            if text_seen and len(in_thread) == len(names):
                break
            await asyncio.sleep(0.5)
        else:
            missing = sorted(set(names) - set(in_thread))
            result.update(
                status="unconfirmed",
                text_in_thread=text_seen,
                attachments_in_thread=in_thread,
                post_responses=[
                    {"status": r["status"], "path": urlparse(r["url"]).path}
                    for r in posts
                ],
                message=(
                    "Send was clicked but Sales Navigator did not confirm "
                    + (
                        f"the attachments {missing}"
                        if text_seen and missing
                        else "the message"
                    )
                    + ". Check the Sales Navigator inbox before retrying."
                ),
            )
            return result

        result.update(
            status="sent",
            attachments_in_thread=in_thread,
            post_responses=[
                {"status": r["status"], "path": urlparse(r["url"]).path} for r in posts
            ],
        )
        return result
    except BaseException:
        if pending["open"]:
            left = await _discard(page, pending["subject"], pending["names"])
            if left:
                logger.error("Attachments left in the InMail composer: %s", left)
        raise
    finally:
        recorder.remove()
        await recorder.drain()
