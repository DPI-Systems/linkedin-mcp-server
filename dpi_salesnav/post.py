"""Publish a feed post as the member: text, and optionally one video.

Measured on account 1's share box (2026-11-15, Spanish interface):

- ``/feed/?shareActive=true`` opens it, at ``/sharing/compose``, as a
  ``<dialog open data-testid=dialog aria-labelledby=dialog-header>``. Its text
  box is ``[componentkey=ShareBox_textEditor]``; its last button is the post
  button, disabled while there is nothing to post.
- The media button is the one of its ``aria-haspopup=dialog`` buttons that
  opens a file chooser (the others are the emoji picker and the menu of other
  post types). Its labels are translated, so it is found by what it does.
- A chosen video opens a second dialog, the media editor (no
  ``aria-labelledby``), whose last button goes back to the post with the video
  in it as a ``<video>``.
- Nothing is uploaded until the post button is clicked: during editing the
  page sends no upload request at all. So a dry run proves the share box took
  the file, not that LinkedIn accepts it; publishing waits for every write
  request the click started before it leaves the page, or it would cut the
  upload short.
- Escape on a box with content asks whether to save a draft: a dialog with a
  close button (``aria-label``) and two unlabelled buttons, discard first.

Selectors are attributes, structure and URLs, never translated text.
"""

import asyncio
import logging
import pathlib
import random
import re
import time
from typing import Any

from dpi_salesnav import failures
from dpi_salesnav.attachments import Attachment
from dpi_salesnav.capture import SalesApiRecorder

logger = logging.getLogger(__name__)

SHARE_URL = "https://www.linkedin.com/feed/?shareActive=true"
OWN_PROFILE_URL = "https://www.linkedin.com/in/me/"
COMPOSER = "dialog[open][aria-labelledby]"
TEXTBOX = '[componentkey="ShareBox_textEditor"]'
SECOND_DIALOG = "dialog[open]:not([aria-labelledby])"
POST_LINK = 'a[href*="/feed/update/urn:li:"]'
TEXT_MAX = 3000
VIDEO_TYPES = {".mp4", ".mov", ".m4v"}
# LinkedIn takes videos up to 5 GB; a short is far below this.
VIDEO_MAX_BYTES = 500 * 1024 * 1024

_OPEN_TIMEOUT_MS = 45_000
_CHOOSER_TIMEOUT_MS = 4_000
_EDITOR_TIMEOUT = 90.0
_DISCARD_TIMEOUT = 15.0
# After the post button: the upload of the video and the post itself.
_PUBLISH_TIMEOUT = 600.0
_WRITE_SETTLE = 5.0
_VERIFY_TIMEOUT = 60.0
_NAV_TIMEOUT_MS = 45_000


class PostRefused(Exception):
    """The share box was not in a state where it was safe to go on."""


def _normalized(text: str) -> str:
    return " ".join(text.split())


async def _pause(low: float = 0.6, high: float = 1.6) -> None:
    await asyncio.sleep(random.uniform(low, high))


def check_video(video: Attachment) -> None:
    if pathlib.Path(video.name).suffix.lower() not in VIDEO_TYPES:
        raise ValueError(
            f"{video.name} is not a video LinkedIn takes here "
            f"({', '.join(sorted(VIDEO_TYPES))})."
        )


async def _open(page: Any) -> Any:
    failures.step("open_share_box")
    await page.goto(SHARE_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    box = page.locator(f"{COMPOSER} {TEXTBOX}").first
    await box.wait_for(state="visible", timeout=_OPEN_TIMEOUT_MS)
    await _pause(1.5, 2.5)
    return box


async def _type(page: Any, box: Any, text: str) -> None:
    failures.step("type_text")
    await box.click()
    await page.keyboard.insert_text(text)
    await _pause()
    typed = _normalized(await box.inner_text())
    if typed != _normalized(text):
        raise PostRefused(
            "The share box did not keep the text that was typed "
            f"({len(typed)} characters shown, {len(_normalized(text))} typed)."
        )


async def _attach_video(page: Any, video: Attachment) -> None:
    failures.step("attach_video")
    composer = page.locator(COMPOSER).first
    candidates = composer.locator("button[aria-haspopup=dialog]")
    chosen = False
    for index in range(await candidates.count()):
        try:
            async with page.expect_file_chooser(timeout=_CHOOSER_TIMEOUT_MS) as chooser:
                await candidates.nth(index).click()
        except Exception:
            # Not the media button (emoji picker, other post types): close
            # what it opened and try the next one.
            await page.keyboard.press("Escape")
            await _pause(0.5, 1.0)
            if not await composer.is_visible():
                raise PostRefused(
                    "The share box closed while looking for its media button."
                ) from None
            continue
        await (await chooser.value).set_files(str(video.path))
        chosen = True
        break
    if not chosen:
        raise PostRefused("No button of the share box opened a file chooser.")

    failures.step("media_editor")
    editor = page.locator(SECOND_DIALOG).filter(has=page.locator("video")).first
    await editor.wait_for(state="visible", timeout=_EDITOR_TIMEOUT * 1000)
    await _pause(1.5, 3.0)
    await editor.locator("button").last.click()
    await editor.wait_for(state="hidden", timeout=_EDITOR_TIMEOUT * 1000)
    await composer.locator("video").first.wait_for(
        state="attached", timeout=_EDITOR_TIMEOUT * 1000
    )
    await _pause()


async def _discard(page: Any) -> bool:
    """Close the share box without saving a draft; True once it is gone."""
    failures.step("discard")
    composer = page.locator(COMPOSER)
    if not await composer.count():
        return True
    await page.keyboard.press("Escape")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DISCARD_TIMEOUT
    while loop.time() < deadline:
        if await page.locator(SECOND_DIALOG).count():
            # Its close button carries an aria-label; of the other two,
            # discard comes first and "save as draft" last.
            unlabelled = page.locator(SECOND_DIALOG).last.locator(
                "button:not([aria-label])"
            )
            if await unlabelled.count() == 2:
                try:
                    await unlabelled.first.click()
                except Exception:
                    # Measured: discarding sends the page back to the feed,
                    # and the click's wait for that navigation can run out
                    # after the click itself is done.
                    logger.debug("Discard click did not settle", exc_info=True)
                await _pause(1.0, 2.0)
        if await _gone(composer):
            return True
        await asyncio.sleep(0.5)
    return await _gone(composer)


async def _gone(composer: Any) -> bool:
    try:
        return not await composer.count()
    except Exception:
        # The page is navigating away from the share box.
        return False


async def _post_links(page: Any) -> set[str]:
    try:
        hrefs = await page.locator(POST_LINK).evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
    except Exception:
        return set()
    return {h for h in hrefs if h}


_POST_SEEN = """(root, want) => {
    const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
    return {
        text: norm(root.innerText || root.textContent).includes(want),
        videos: root.querySelectorAll('video').length,
    };
}"""


async def _verify(
    page: Any, text: str, video: bool, new_links: list[str]
) -> dict[str, Any]:
    """Load the post as LinkedIn serves it and check its text (and video)."""
    out: dict[str, Any] = {"verified": False}
    want = _normalized(text)[:200]
    targets = [
        f"https://www.linkedin.com{h}" if h.startswith("/") else h for h in new_links
    ]
    try:
        if not targets:
            # No link to the new post appeared: look for it among the
            # member's own recent posts.
            await page.goto(
                OWN_PROFILE_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
            )
            vanity = re.search(r"/in/([^/?#]+)", page.url)
            if not vanity or vanity.group(1) == "me":
                out["error"] = f"Could not resolve the own profile from {page.url}"
                return out
            targets = [
                f"https://www.linkedin.com/in/{vanity.group(1)}/recent-activity/all/"
            ]
        loop = asyncio.get_running_loop()
        for target in targets[:2]:
            await page.goto(
                target, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
            )
            deadline = loop.time() + _VERIFY_TIMEOUT
            while True:
                seen = await page.locator("body").evaluate(_POST_SEEN, want)
                found = seen["text"] and (not video or seen["videos"] > 0)
                if found or loop.time() >= deadline:
                    break
                await asyncio.sleep(2.0)
            out.update(url=page.url, text=seen["text"], videos=seen["videos"])
            if found:
                out["verified"] = True
                break
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    if not out["verified"]:
        logger.warning("Published post not found: %s", out)
    return out


async def _wait_for_publish(
    page: Any, recorder: SalesApiRecorder, clicked_at: float, links_before: set[str]
) -> list[str]:
    """Until the share box is gone and every write the click started is done.

    Navigating away earlier would cancel the video upload. Returns links to
    posts that appeared on the page after the click (the "view post" notice).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PUBLISH_TIMEOUT
    quiet_since: float | None = None
    new_links: list[str] = []
    while loop.time() < deadline:
        new_links = sorted(await _post_links(page) - links_before)
        try:
            closed = not await page.locator(COMPOSER).count()
        except Exception:
            closed = False
        if closed and recorder.open_writes_since(clicked_at) == 0:
            quiet_since = quiet_since or loop.time()
            if new_links or loop.time() - quiet_since >= _WRITE_SETTLE:
                return new_links
        else:
            quiet_since = None
        await asyncio.sleep(1.0)
    logger.warning(
        "Post still not done after %ss (%d writes open)",
        _PUBLISH_TIMEOUT,
        recorder.open_writes_since(clicked_at),
    )
    return new_links


async def create_post(
    page: Any,
    text: str,
    *,
    video: Attachment | None = None,
    confirm_publish: bool = False,
) -> dict[str, Any]:
    """Write a feed post; publish it only with *confirm_publish*."""
    if not text.strip():
        raise ValueError("A post needs text.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"A post takes at most {TEXT_MAX} characters.")
    if video is not None:
        check_video(video)
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        box = await _open(page)
        await _type(page, box, text)
        if video is not None:
            await _attach_video(page, video)
        failures.step("find_post_button")
        button = page.locator(COMPOSER).first.locator("button").last
        enabled = await button.is_enabled()
        result: dict[str, Any] = {
            "text_chars": len(text),
            "video": video.describe() if video else None,
            "post_button_enabled": enabled,
        }
        if not confirm_publish:
            if not await _discard(page):
                raise PostRefused(
                    "Dry run: the share box would not close. Nothing was "
                    "published; check LinkedIn for a draft before posting again."
                )
            result.update(
                status="preview",
                message=(
                    "Nothing was published. The share box took the text"
                    + (
                        " and the video (LinkedIn uploads a video only when "
                        "the post is published)"
                        if video
                        else ""
                    )
                    + ". Call again with confirm_publish=true to publish."
                ),
            )
            return result
        if not enabled:
            await _discard(page)
            raise PostRefused("The post button stayed disabled; nothing was published.")

        links_before = await _post_links(page)
        recorder.posts.clear()
        failures.step("publish")
        clicked_at = time.monotonic()
        await button.click()
        # From here on nothing raises into a retry: the post may be out.
        failures.step("wait_for_upload")
        new_links: list[str] = []
        try:
            new_links = await _wait_for_publish(
                page, recorder, clicked_at, links_before
            )
        except Exception:
            logger.warning("The share box stopped answering after post", exc_info=True)
        await recorder.drain()
        upload_seconds = round(time.monotonic() - clicked_at, 1)
        failures.step("verify_post")
        verified = await _verify(page, text, video is not None, new_links)
        result.update(
            upload_seconds=upload_seconds,
            post_links=new_links,
            verified_after_reload=verified,
            post_responses=list(recorder.posts),
        )
        if verified["verified"]:
            result.update(status="published", url=verified.get("url"))
        else:
            result.update(
                status="unconfirmed",
                message=(
                    "Post was clicked but the post was not found where "
                    "LinkedIn serves it. Do not post again: check the "
                    "profile's recent activity first."
                ),
            )
        return result
    finally:
        recorder.remove()
        await recorder.drain()
