"""Hand the server's own browser to a person for a LinkedIn verification.

When LinkedIn stops the account at a checkpoint (a captcha, a code by email,
the restriction page), a tool can only report it: solving it is a person's job.
In the container the browser is an ordinary headed window on the Xvfb display,
so the same x11vnc + noVNC pair that ``--login --login-viewer`` uses can show
that very window. Same browser, same profile, same session: nothing is logged
in again and nothing is rotated.

Three steps, one tool each: open (returns the viewer URL), status (reads where
the page is, without navigating) and finish (removes the viewer and closes the
browser, so the next tool call opens it again and validates the session the
normal way, and the cookies are exported on the way out).

The browser is opened here without the feed check that ``get_or_create_browser``
runs, because that check is exactly what fails on a checkpoint and closes the
browser before anyone could see it. Everything else mirrors upstream's
``_create_browser``: the profile lease, the guardian, the cookie export path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any

from linkedin_mcp_server.bootstrap import get_bootstrap_state
from linkedin_mcp_server.core.auth import (
    _is_account_restricted_url,
    _is_auth_blocker_url,
)
from linkedin_mcp_server.core.destination import is_linkedin_landing
from linkedin_mcp_server.drivers import browser as drivers
from linkedin_mcp_server.error_handler import CodedToolError, ErrorCode
from linkedin_mcp_server.exceptions import BrowserBusyError
from linkedin_mcp_server.login_viewer import VIEWER_PORT, LoginViewer
from linkedin_mcp_server.process_tree import (
    release_browser_guardian,
    start_browser_guardian,
)
from linkedin_mcp_server.profile_lease import get_profile_lease
from linkedin_mcp_server.session_state import (
    get_runtime_id,
    load_source_state,
    portable_cookie_path,
)

from dpi_salesnav.capture import CHALLENGE_MARKERS

logger = logging.getLogger(__name__)

#: How long the viewer stays up before it is removed on its own.
VIEWER_SECONDS = 1800
#: Public origin that reaches the container's port 6080 (Caddy on dpi-lab).
PUBLIC_URL_ENV = "DPI_VIEWER_PUBLIC_URL"
_LOCAL_ORIGIN = f"http://127.0.0.1:{VIEWER_PORT}"
_FEED = "https://www.linkedin.com/feed/"
#: Longest a status call may wait for the check to clear.
MAX_WAIT_SECONDS = 600
_POLL_SECONDS = 2.0


class VerificationUnavailable(CodedToolError):
    """The viewer cannot be offered in this process."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.BROWSER_UNAVAILABLE)


@dataclass
class _Session:
    viewer: LoginViewer
    url: str
    expires_at: float
    timer: asyncio.Task[None] | None = None


_session: _Session | None = None
_lock = asyncio.Lock()


def public_url(local_url: str, base: str | None = None) -> str:
    """Rewrite the viewer's loopback address onto the configured public origin."""
    base = (os.environ.get(PUBLIC_URL_ENV, "") if base is None else base).strip()
    if not base or not local_url.startswith(_LOCAL_ORIGIN):
        return local_url
    return base.rstrip("/") + local_url[len(_LOCAL_ORIGIN) :]


def page_state(url: str, frame_urls: tuple[str, ...] = ()) -> str:
    """Where the page stands, from addresses alone (never from its text).

    ``restricted`` is LinkedIn's account-restriction route, ``challenge`` any
    sign-in, authwall or checkpoint route, or a page with a human-verification
    frame inside it (Sales Navigator's captcha keeps the search URL), ``ok``
    any other LinkedIn page and ``off_linkedin`` a blank or foreign page.
    """
    if not is_linkedin_landing(url):
        return "off_linkedin"
    if _is_account_restricted_url(url):
        return "restricted"
    if _is_auth_blocker_url(url):
        return "challenge"
    if any(m in frame for frame in frame_urls for m in CHALLENGE_MARKERS):
        return "challenge"
    return "ok"


def _require_viewer_host() -> None:
    missing = [n for n in ("x11vnc", "websockify", "openbox") if not shutil.which(n)]
    if missing or not os.environ.get("DISPLAY"):
        raise VerificationUnavailable(
            "The verification viewer only works in the Docker image, where the "
            "browser runs on a virtual display (missing: "
            f"{', '.join(missing) or 'DISPLAY'}). Run locally with --no-headless "
            "and solve it in the window instead."
        )


async def _browser_without_validation() -> drivers.BrowserManager:
    """The singleton browser, opened on the source profile without a feed check."""
    if drivers._browser is not None:
        return drivers._browser
    async with drivers._browser_create_lock, drivers._browser_lifecycle_lock:
        if drivers._browser is not None:
            return drivers._browser
        profile = drivers.get_profile_dir()
        state = load_source_state(profile)
        if state is None or not drivers.profile_exists(profile):
            raise VerificationUnavailable(
                "There is no stored LinkedIn session to verify. Create one with "
                "--login --login-viewer."
            )
        if state.source_runtime_id != get_runtime_id():
            # A foreign runtime works on a copy rebuilt from cookies.json, so a
            # verification there would not reach the stored session.
            raise VerificationUnavailable(
                f"The session was created by runtime {state.source_runtime_id}, "
                f"not this one ({get_runtime_id()}); verify it where it was created."
            )
        lease = get_profile_lease()
        if lease.browser_open or drivers._browser_lease is not None:
            raise BrowserBusyError(
                "A previous browser on this profile did not shut down cleanly "
                "and may still be running. Restart the server to recover."
            )
        if not lease.try_acquire():
            raise BrowserBusyError()
        start_browser_guardian(lease.guardian_fd())
        options, viewport = drivers._launch_options()
        manager = drivers._make_browser(
            profile, launch_options=options, viewport=viewport
        )
        try:
            await manager.start()
            await drivers._restore_portable_session_if_the_store_lost_it(
                manager, profile
            )
            drivers._apply_browser_settings(manager)
        except BaseException:
            closed, _ = await drivers._close_holding_back_cancels(manager)
            if not closed:
                # Chromium may still be on the profile: keep it held, as
                # upstream does, so nothing launches on top of it.
                lease.mark_browser_open()
                drivers._browser_lease = lease
                raise
            release_browser_guardian()
            lease.release()
            raise
        drivers._browser = manager
        drivers._browser_cookie_export_path = portable_cookie_path(profile)
        drivers._browser_lease = lease
        lease.mark_browser_open()
        return manager


def is_open() -> bool:
    """Whether a person may be working in the viewer right now."""
    return _session is not None


def _describe(
    where: tuple[str, tuple[str, ...]] | None, *, open_: bool
) -> dict[str, Any]:
    url = None if where is None else where[0]
    state = "no_browser" if where is None else page_state(*where)
    out: dict[str, Any] = {"open": open_, "state": state, "page_url": url}
    if open_ and _session is not None:
        out["viewer_url"] = _session.url
        out["expires_in_seconds"] = max(0, round(_session.expires_at - time.time()))
    return out


def _current() -> tuple[str, tuple[str, ...]] | None:
    """The page's address and those of its frames, or None without a page."""
    manager = drivers._browser
    if manager is None:
        return None
    try:
        page = manager.page
        frames = tuple(f.url for f in getattr(page, "frames", ()) if f is not None)
        return page.url, frames
    except Exception:
        return None


async def open_verification(seconds: int = VIEWER_SECONDS) -> dict[str, Any]:
    """Show the server's browser through noVNC and return the viewer URL."""
    global _session
    async with _lock:
        if _session is not None:
            return {**_describe(_current(), open_=True), "already_open": True}
        _require_viewer_host()
        manager = await _browser_without_validation()
        # Counts as a call in flight, so the idle timer leaves the browser up
        # for as long as somebody may be typing into it.
        drivers.note_call_started()
        viewer = LoginViewer()
        try:
            if not is_linkedin_landing(manager.page.url):
                await manager.page.goto(_FEED, wait_until="domcontentloaded")
            await manager.page.bring_to_front()
            await asyncio.to_thread(viewer.start_window_manager)
            local = await asyncio.to_thread(viewer.start_remote_control)
        except BaseException:
            await asyncio.to_thread(_stop_quietly, viewer)
            drivers.note_activity()
            raise
        _session = _Session(viewer, public_url(local), time.time() + seconds)
        _session.timer = asyncio.create_task(_expire(seconds))
        logger.info("Verification viewer open for %ss", seconds)
        return {**_describe(_current(), open_=True), "already_open": False}


async def verification_status(wait_seconds: float = 0) -> dict[str, Any]:
    """Where the page is now, read from addresses only; never navigates.

    With ``wait_seconds``, waits up to that long for the page to leave the
    check (``state`` other than ``challenge`` and ``restricted``), so a caller
    can wait for the person without loading anything again.
    """
    wait_seconds = max(0.0, min(float(wait_seconds), MAX_WAIT_SECONDS))
    deadline = time.monotonic() + wait_seconds
    while True:
        out = _describe(_current(), open_=_session is not None)
        if out["state"] not in ("challenge", "restricted"):
            break
        if _session is None or time.monotonic() >= deadline:
            break
        await asyncio.sleep(_POLL_SECONDS)
    out["waited_seconds"] = round(wait_seconds - max(0.0, deadline - time.monotonic()))
    return out


async def finish_verification(*, reason: str = "finished") -> dict[str, Any]:
    """Remove the viewer and close the browser so the next call starts clean."""
    global _session
    async with _lock:
        where = _current()
        url = None if where is None else where[0]
        before = _describe(where, open_=False)
        session, _session = _session, None
        if session is not None:
            if (
                session.timer is not None
                and session.timer is not asyncio.current_task()
            ):
                session.timer.cancel()
            await asyncio.to_thread(_stop_quietly, session.viewer)
            drivers.note_activity()
        if before["state"] == "ok":
            # The person cleared the check, so a restriction latched earlier in
            # this process no longer describes the account.
            get_bootstrap_state().account_restricted = False
        if drivers._browser is not None:
            # Exports cookies.json on the way out; the next tool call reopens
            # the browser and runs the usual feed validation.
            await drivers.close_browser()
        logger.info("Verification viewer closed (%s), page was %s", reason, url)
        return {**before, "closed": session is not None, "reason": reason}


async def _expire(seconds: int) -> None:
    await asyncio.sleep(seconds)
    try:
        await finish_verification(reason="expired")
    except Exception:
        logger.warning("Could not close the expired verification viewer", exc_info=True)


def _stop_quietly(viewer: LoginViewer) -> None:
    try:
        viewer.stop_window_manager()
    except Exception:
        logger.warning("Verification viewer did not stop cleanly", exc_info=True)


def reset_for_testing() -> None:
    global _session
    _session = None
