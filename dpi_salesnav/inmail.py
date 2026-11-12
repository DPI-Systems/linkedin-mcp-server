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
import pathlib
import random
import re
import tempfile
import time
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.throttle import throttle_evidence, throttled_count

from dpi_salesnav import failures, page_health
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
# Measured on account 2: a lead page that finished loading a few seconds
# after 20 s, with its message button there, and no Sales API error.
_OPENER_TIMEOUT_MS = 60_000
_COMPOSER_TIMEOUT_MS = 15_000
# The server's default of 5 s is for reading a page that is already there.
# Measured on account 2: the reload of a stuck lead page ran out at 5 s.
_GOTO_TIMEOUT_MS = 45_000
_THREAD_ATTACHMENT_TIMEOUT = 60.0
# Before reloading after send: at least this long, and until the POSTs the
# click started are answered, up to the second figure.
_POST_SETTLE = 2.0
_POST_TIMEOUT = 30.0
# How long the reloaded lead page gets to list the message just sent.
_VERIFY_TIMEOUT = 30.0
INMAIL_TOOL_TIMEOUT = 360.0
_LEAD_URL = re.compile(r"^/sales/lead/[^/]+,[^/]+,[^/]+/?$")


def _normalized(text: str) -> str:
    return " ".join(text.split())


# The conversation history in the open dialog, without the compose form (and
# so without what is being typed).
_THREAD_TEXT = """(root, form) => {
    const clone = root.cloneNode(true);
    clone.querySelectorAll(form + ', input, textarea, script, style')
        .forEach(e => e.remove());
    return clone.textContent || '';
}"""


async def _thread_text(page: Any) -> str:
    return _normalized(await page.locator(DIALOG).first.evaluate(_THREAD_TEXT, FORM))


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


_LEAD_PAGE_STATE = """async () => {
    const shown = {
        visibility: document.visibilityState,
        focus: document.hasFocus(),
        title: document.title,
        anchors: [...document.querySelectorAll('button')]
            .filter(b => [...b.attributes].some(a => a.name.startsWith('data-anchor')))
            .map(b => Object.fromEntries([...b.attributes]
                .filter(a => a.name !== 'class' && a.name !== 'style')
                .map(a => [a.name, a.value.slice(0, 60)]))),
        dialogs: [...document.querySelectorAll(
            '[role=dialog], [role=alertdialog], dialog[open]')].map(d => ({
                role: d.getAttribute('role') || d.tagName.toLowerCase(),
                label: (d.getAttribute('aria-label') || '').slice(0, 80),
                shown: d.checkVisibility ? d.checkVisibility() : !!d.offsetParent,
                message_overlay: d.hasAttribute('data-message-overlay-container'),
            })),
        body_chars: document.body ? document.body.innerText.length : 0,
        ready: document.readyState,
    };
    // Frames the page got in one second: about 60 when it is painted, few or
    // none when the browser holds it back as a background tab.
    let frames = 0;
    const t0 = performance.now();
    await new Promise(done => {
        const tick = () => {
            frames++;
            if (performance.now() - t0 < 1000) requestAnimationFrame(tick);
            else done();
        };
        requestAnimationFrame(tick);
        setTimeout(done, 1500);
    });
    shown.raf_frames_1s = frames;
    return shown;
}"""

# The same page read again right after the screenshot, to tell whether taking
# one is what makes a stuck page render.
_AFTER_SCREENSHOT = """() => ({
    title: document.title,
    body_chars: document.body ? document.body.innerText.length : 0,
    has_message_button: !!document.querySelector('button[data-anchor-send-inmail]'),
})"""

# What Sales Navigator titles its tab while the app is still on its splash.
# A product name, not translated; a lead page that rendered is titled after
# the person.
_SPLASH_TITLE = "Sales Navigator"
_STATE_TIMEOUT = 5.0


async def _describe_lead_page(
    page: Any, recorder: SalesApiRecorder | None = None
) -> dict[str, Any]:
    """What the lead page showed when its message button never appeared."""
    state: dict[str, Any] = {}
    try:
        state = await asyncio.wait_for(page.evaluate(_LEAD_PAGE_STATE), _STATE_TIMEOUT)
    except Exception as e:
        state = {"error": repr(e)[:200]}
    if recorder is not None:
        state["pending_requests"] = recorder.pending_requests()
    state["longtasks"] = await _long_tasks(page)
    try:
        shot = pathlib.Path(tempfile.gettempdir()) / "inmail-no-button.png"
        started = time.monotonic()
        await page.screenshot(path=str(shot), full_page=False)
        state["screenshot_ms"] = round((time.monotonic() - started) * 1000)
        state["screenshot"] = str(shot)
    except Exception:
        pass
    try:
        state["after_screenshot"] = await asyncio.wait_for(
            page.evaluate(_AFTER_SCREENSHOT), _STATE_TIMEOUT
        )
    except Exception as e:
        state["after_screenshot"] = {"error": repr(e)[:200]}
    logger.warning("Lead page without a message button: %s", state)
    return state


async def _long_tasks(page: Any) -> Any:
    try:
        return await asyncio.wait_for(
            page.evaluate(page_health.LONG_TASKS), _STATE_TIMEOUT
        )
    except Exception as e:
        return {"error": repr(e)[:200]}


def _half_rendered(state: dict[str, Any]) -> bool:
    """The app got past its splash: the person's title or some body text.

    Measured on account 2: such a page, reloaded, went back to the splash for
    another full wait, while waiting on it is what can still bring the button.
    """
    for shown in (state, state.get("after_screenshot") or {}):
        if (shown.get("body_chars") or 0) > 0:
            return True
        title = (shown.get("title") or "").strip()
        if title and title != _SPLASH_TITLE:
            return True
    return False


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


async def _verify_after_reload(
    page: Any,
    lead_url: str,
    body: str,
    subject: str | None,
    names: list[str],
    sent_before: int,
) -> dict[str, Any]:
    """Whether the lead's thread, loaded again, holds the message just sent.

    Never raises: send was clicked, so a failure here can only make the
    outcome unknown, never a reason to send again.
    """
    out: dict[str, Any] = {"verified": False, "text": False}
    loop = asyncio.get_running_loop()
    want = _normalized(body)
    opened = False
    try:
        await page.goto(
            lead_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS
        )
        opener = page.locator(OPEN_COMPOSER).first
        await opener.wait_for(state="visible", timeout=_OPENER_TIMEOUT_MS)
        await opener.click()
        opened = True
        await page.locator(DIALOG).first.wait_for(
            state="visible", timeout=_COMPOSER_TIMEOUT_MS
        )
        deadline = loop.time() + _VERIFY_TIMEOUT
        while True:
            thread = await _thread_text(page)
            copies = thread.count(want)
            out["text"] = copies > sent_before
            if names:
                out["attachments"] = (
                    await _names_present(page, names, scope=DIALOG, exclude=FORM)
                )["found"]
            # The subject, when the composer had one: measured on account 2,
            # the reloaded thread shows an InMail's subject.
            if subject:
                out["subject"] = _normalized(subject) in thread
            if (
                out["text"]
                and out.get("subject", True)
                and len(out.get("attachments") or []) == len(names)
            ):
                out["verified"] = True
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(1.0)
        out["copies"] = copies
        out["copies_before"] = sent_before
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if opened:
            try:
                await page.locator(CLOSE).first.click(timeout=5_000)
            except Exception:
                logger.debug("Could not close the verification dialog", exc_info=True)
    if not out["verified"]:
        logger.warning("Sent message not found after reloading %s: %s", lead_url, out)
    return out


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
        opener = page.locator(OPEN_COMPOSER).first
        # Measured on account 2: a lead page sometimes never shows its message
        # button within the wait. One that got past the splash gets a second
        # wait on the same page; one still on the splash is loaded once more.
        # Nothing is typed yet, so either is safe.
        loop = asyncio.get_running_loop()
        load_seconds = 0.0
        attempts = 1
        path = "first"
        failures.step("load_lead_page")
        started = loop.time()
        await page.goto(
            lead_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS
        )
        for attempt in range(2):
            failures.step("find_message_button")
            cpu_before, waited_from = page_health.renderer_cpu(), loop.time()
            try:
                await opener.wait_for(state="visible", timeout=_OPENER_TIMEOUT_MS)
                load_seconds = round(loop.time() - started, 1)
                renderer_cpu = page_health.cpu_since(
                    cpu_before, loop.time() - waited_from
                )
                break
            except Exception as e:
                _raise_if_throttled()
                await recorder.drain()
                refused = [
                    {"status": r["status"], "path": urlparse(r["url"]).path}
                    for r in recorder.records
                    if r["status"] >= 400
                ]
                renderer_cpu = page_health.cpu_since(
                    cpu_before, loop.time() - waited_from
                )
                state = await _describe_lead_page(page, recorder)
                state["renderer_cpu"] = renderer_cpu
                if attempt == 1:
                    raise SendRefused(
                        "This lead page has no Sales Navigator message button, "
                        f"twice ({path}; page ended at {page.url}); nothing was "
                        f"typed or sent. Sales API errors: {refused}. "
                        f"Page state: {state}"
                    ) from e
                if _half_rendered(state):
                    path = "waited_more"
                    logger.warning(
                        "No message button yet on %s, page past its splash "
                        "(sales-api errors %s); waiting on it once more",
                        lead_url,
                        refused,
                    )
                    continue
                stuck = "error" in state and "error" in (
                    state.get("after_screenshot") or {}
                )
                path = "new_page" if stuck else "reloaded"
                logger.warning(
                    "No message button on %s, %s (sales-api errors %s); %s",
                    lead_url,
                    "page not answering" if stuck else "still on the splash",
                    refused,
                    "loading it in a new page" if stuck else "reloading",
                )
                await _human_pause(5.0, 9.0)
                failures.step("load_lead_page")
                if stuck:
                    # A stuck renderer will not let go of its document, so a
                    # navigation from it times out (measured on account 2).
                    recorder.remove()
                    page = await page_health.replace_page(page)
                    failures.use_page(page)
                    recorder = SalesApiRecorder(page)
                    recorder.install()
                    opener = page.locator(OPEN_COMPOSER).first
                attempts = 2
                started = loop.time()
                await page.goto(
                    lead_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS
                )
        long_tasks = await _long_tasks(page)
        await _human_pause()
        failures.step("open_composer")
        await opener.click()
        await page.locator(BODY).wait_for(state="visible", timeout=_COMPOSER_TIMEOUT_MS)
        _raise_if_throttled()

        recipient = await page.locator(DIALOG).first.get_attribute("aria-label")
        has_subject = await page.locator(SUBJECT).count() > 0
        pending.update(open=True, subject=has_subject)
        failures.step("type_text")
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
        failures.step("find_send_button")
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
            failures.step("attach")
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
            # From goto to a visible message button, on the load that showed it.
            "lead_load_seconds": load_seconds,
            "lead_load_attempts": attempts,
            # first: shown within one wait; waited_more: a second wait on the
            # same page; reloaded: the page was loaded again; new_page: it
            # stopped answering and was loaded in a fresh page instead.
            "lead_load_path": path,
            # During the wait that showed the button: CPU of the renderer
            # processes, and the page's longest tasks since it loaded.
            "lead_load_renderer_cpu": renderer_cpu,
            "lead_load_longtasks": long_tasks,
        }

        if not confirm_send:
            failures.step("dry_run_cleanup")
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

        # The same text may already be in this lead's thread (a message sent
        # before); only a copy beyond those counts as this one.
        sent_before = (await _thread_text(page)).count(_normalized(body))
        recorder.records.clear()
        recorder.posts.clear()
        pending["open"] = False
        failures.step("send")
        clicked_at = time.monotonic()
        await send.click()
        failures.step("confirm_in_thread")
        deadline = (
            loop.time() + _SEND_TIMEOUT + (_THREAD_ATTACHMENT_TIMEOUT if names else 0)
        )
        in_thread: list[str] = []
        text_seen = False
        try:
            while loop.time() < deadline:
                await recorder.drain()
                _raise_if_throttled()
                cleared = (await page.locator(BODY).input_value()) == ""
                # What the page itself shows after the click; recorded, and
                # a reason to stop waiting, but not proof (see below).
                thread = await page.locator(DIALOG).first.inner_text()
                text_seen = cleared and _normalized(body) in _normalized(thread)
                if names:
                    in_thread = (
                        await _names_present(page, names, scope=DIALOG, exclude=FORM)
                    )["found"]
                if text_seen and len(in_thread) == len(names):
                    break
                await asyncio.sleep(0.5)
        except RateLimitError:
            raise
        except Exception:
            # Send was clicked: a page that stops answering now leaves the
            # outcome to the reload below, not to a retry.
            logger.warning("The composer stopped answering after send", exc_info=True)
        await recorder.drain()
        # What the open composer shows is drawn by the page and is no proof:
        # measured on account 2, three messages showed in it, file included,
        # and never reached LinkedIn (no thread in the inbox, no credit spent).
        # Only the lead's thread as LinkedIn serves it again counts.
        # The page draws the message before its request is answered, so a
        # reload right away could cancel the send itself. Wait for the POSTs
        # the click started, and give one at least the time to start.
        failures.step("wait_for_send_request")
        settle = loop.time() + _POST_SETTLE
        post_deadline = loop.time() + _POST_TIMEOUT
        while loop.time() < post_deadline:
            open_posts = recorder.open_posts_since(clicked_at)
            if open_posts == 0 and loop.time() >= settle:
                break
            await asyncio.sleep(0.25)
        else:
            logger.warning(
                "%d POSTs still open %ss after send; reloading anyway",
                recorder.open_posts_since(clicked_at),
                _POST_TIMEOUT,
            )
        await recorder.drain()
        failures.step("verify_after_reload")
        verified = await _verify_after_reload(
            page, lead_url, body, typed_subject, names, sent_before
        )
        # Every POST answered since the click, the reload's included: the
        # send's own request is among them, whichever host it went to.
        result.update(
            text_in_thread=text_seen,
            attachments_in_thread=in_thread,
            post_responses=list(recorder.posts),
            verified_after_reload=verified,
        )
        if verified["verified"]:
            result["status"] = "sent"
            return result
        missing = sorted(set(names) - set(verified.get("attachments") or []))
        result.update(
            status="unconfirmed",
            message=(
                "Send was clicked but the message is not in this lead's thread "
                "after reloading the page"
                + (
                    f" (attachments missing: {missing})"
                    if verified.get("text") and missing
                    else ""
                )
                + ". Do not retry: check the Sales Navigator inbox and the "
                "InMail credits first."
            ),
        )
        return result
    except BaseException as exc:
        if isinstance(exc, Exception):
            # Before the composer is emptied and closed, which is what the
            # screenshot would show otherwise.
            await failures.snapshot(page)
        if pending["open"]:
            left = await _discard(page, pending["subject"], pending["names"])
            if left:
                logger.error("Attachments left in the InMail composer: %s", left)
        raise
    finally:
        recorder.remove()
        await recorder.drain()
