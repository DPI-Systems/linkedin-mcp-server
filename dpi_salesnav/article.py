"""Write a LinkedIn article (the /pulse/ long-form format) from Markdown.

Measured on the editor at /article/new/ (2026-11-04):

- The title is a textarea and the body a ProseMirror editor. Rich text goes in
  as a pasted ``text/html`` clipboard, which keeps headings, bold, italic,
  links, both list kinds, quotes, code blocks and dividers.
- An image goes in as a pasted file and is uploaded by the editor itself; it
  is done once its ``src`` points at LinkedIn's media host.
- The cover is a file chooser behind the cover button, followed by a crop
  dialog that must be confirmed with its last button.
- The editor saves a draft by itself; its URL becomes /article/edit/<id>/.
- "Publish as" (member or page) and "publish in" (standalone article or a
  newsletter) are radios in one dropdown. A newsletter edition is mailed to
  its subscribers, so the default here is a standalone article.
- Publishing is the editor's next button, then a share dialog with a
  commentary box and a publish button (its last button).

Selectors are ids and attributes the editor sets for itself. Two controls
carry nothing but translated text, the cover button and the editor's next
button; those are looked up in a per-locale table, as AGENTS.md asks.
"""

import asyncio
import base64
import logging
import pathlib
import random
import re
from typing import Any

from dpi_salesnav.article_markdown import HtmlBlock, ImageBlock, plan_article

logger = logging.getLogger(__name__)

NEW_ARTICLE_URL = "https://www.linkedin.com/article/new/"
TITLE = "#article-editor-headline__textarea"
BODY = "[data-test-article-editor-content-textbox]"
ENTITY_RADIO = '[id^="article-editor-entity-selector-item-radio-"]'
COVER_DIALOG = (
    '[role=dialog][aria-labelledby="article-editor-media-editor-modal-header"]'
)
SHARE_DIALOG = '[role=dialog][aria-labelledby="share-to-linkedin-modal__header"]'
TITLE_MAX = 150
IMAGE_MAX_BYTES = 8 * 1024 * 1024
_DRAFT_URL = re.compile(r"/article/edit/(\d+)")
_IMAGE_UPLOAD_TIMEOUT = 60.0
# The editor is slower to load than the 5 s navigation default.
_NAV_TIMEOUT = 30_000

# Text is the only thing these two buttons carry. Add a locale here when the
# account's interface language changes.
COVER_BUTTON_TEXT = ("Cargar desde el ordenador", "Upload from computer")
NEXT_BUTTON_TEXT = ("Siguiente", "Next")


class ArticleError(Exception):
    """The editor was not in a state where it is safe to go on."""


async def _pause(low: float = 0.6, high: float = 1.8) -> None:
    await asyncio.sleep(random.uniform(low, high))


_PASTE_HTML = """([selector, html]) => {
  const ed = document.querySelector(selector);
  ed.focus();
  const sel = getSelection(); sel.selectAllChildren(ed); sel.collapseToEnd();
  const dt = new DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', html.replace(/<[^>]+>/g, ' '));
  ed.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}"""

_PASTE_IMAGE = """([selector, b64, name, type]) => {
  const ed = document.querySelector(selector);
  ed.focus();
  const sel = getSelection(); sel.selectAllChildren(ed); sel.collapseToEnd();
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bin], name, {type}));
  ed.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  return ed.querySelectorAll('figure img').length;
}"""

_FIGURES = (
    """(selector) => document.querySelectorAll(selector + ' figure img').length"""
)

_SAVED_SUMMARY = """(selector) => {
  const ed = document.querySelector(selector);
  const count = q => ed.querySelectorAll(q).length;
  return {
    images_uploaded: [...ed.querySelectorAll('figure img')]
      .filter(i => i.src.startsWith('https://media.licdn.com/')).length,
    images_in_body: count('figure img'),
    headings_large: count('h2'), headings_small: count('h3'),
    lists: count('ul') + count('ol'), quotes: count('blockquote'),
    code_blocks: count('pre'), links: count('a[href]'),
    characters: ed.innerText.length,
  };
}"""

_SET_CAPTION = """([selector, index, caption]) => {
  const box = document.querySelectorAll(selector + ' figure textarea')[index];
  if (!box) return false;
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  set.call(box, caption);
  box.dispatchEvent(new Event('input', {bubbles: true}));
  return true;
}"""

# Each option is a button[role=radio] with the radio input inside it.
_ENTITY_OPTIONS = """(radio) => [...document.querySelectorAll(radio)].map(input => {
  const button = input.closest('button[role=radio]');
  return {id: input.id, urn: input.id.replace('article-editor-entity-selector-item-radio-', ''),
          name: button ? button.innerText.trim() : '',
          checked: button ? button.getAttribute('aria-checked') === 'true' : null};
})"""

_CLICK_ENTITY = """(id) => {
  const button = document.getElementById(id)?.closest('button[role=radio]');
  if (!button) return false;
  if (button.getAttribute('aria-checked') !== 'true') button.click();
  return true;
}"""


def _image_payload(block: ImageBlock) -> tuple[str, str, str]:
    data = block.path.read_bytes()
    if len(data) > IMAGE_MAX_BYTES:
        raise ValueError(f"Image {block.path.name} is over 8 MB")
    suffix = block.path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
    return base64.b64encode(data).decode(), block.path.name, mime


async def _wait_for_figure(page: Any, expected: int) -> None:
    """The pasted image is in the body. Its upload is checked on the saved draft."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _IMAGE_UPLOAD_TIMEOUT
    while loop.time() < deadline:
        if await page.evaluate(_FIGURES, BODY) >= expected:
            await _pause(2.0, 4.0)
            return
        await asyncio.sleep(1.0)
    raise ArticleError(f"The editor did not place image {expected}.")


async def _check_saved_draft(page: Any, draft_url: str) -> dict[str, Any]:
    """Reopen the draft and report what LinkedIn actually stored."""
    await asyncio.sleep(4.0)  # let the last autosave land
    await page.goto(draft_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    await page.locator(BODY).wait_for(state="visible", timeout=30_000)
    await _pause(2.0, 3.0)
    return await page.evaluate(_SAVED_SUMMARY, BODY)


async def _button_by_text(
    page: Any, texts: tuple[str, ...], *, outside_dialogs: bool
) -> Any:
    for text in texts:
        locator = page.get_by_role("button", name=text, exact=True)
        if outside_dialogs:
            locator = locator.filter(
                has_not=page.locator("xpath=ancestor::*[@role='dialog']")
            )
        if await locator.count() == 1:
            return locator
    raise ArticleError(
        f"Could not find exactly one button labelled any of {list(texts)}; "
        "the interface language may need adding to the table in article.py."
    )


async def _set_cover(page: Any, cover: pathlib.Path) -> None:
    button = await _button_by_text(page, COVER_BUTTON_TEXT, outside_dialogs=True)
    async with page.expect_file_chooser(timeout=15_000) as chooser:
        await button.click()
    await (await chooser.value).set_files(str(cover))
    dialog = page.locator(COVER_DIALOG)
    await dialog.wait_for(state="visible", timeout=20_000)
    await _pause()
    await dialog.locator("button").last.click()
    await dialog.wait_for(state="hidden", timeout=20_000)


async def _open_entity_menu(page: Any) -> None:
    """Open the dropdown whose options are the author radios.

    Its options only exist once it is open, and its trigger carries only the
    member's name, so each of the editor's dropdowns (outside the global nav)
    is tried until one shows the radios.
    """
    triggers = page.locator("button[aria-expanded]").filter(
        has_not=page.locator("xpath=ancestor::header | ancestor::nav")
    )
    for index in range(await triggers.count()):
        trigger = triggers.nth(index)
        if not await trigger.is_visible():
            continue
        await trigger.click()
        await _pause(0.8, 1.5)
        if await page.locator(ENTITY_RADIO).count():
            return
        await page.keyboard.press("Escape")
        await _pause(0.3, 0.8)
    raise ArticleError("Could not find the editor's author menu.")


async def _choose_entities(
    page: Any, author: str | None, publish_in: str | None
) -> dict[str, str]:
    """Pick "publish as" and "publish in"; returns what ended up selected."""
    await _open_entity_menu(page)
    options = await page.evaluate(_ENTITY_OPTIONS, ENTITY_RADIO)
    if any(o["checked"] is None or not o["name"] for o in options):
        raise ArticleError("Could not read the editor's author options.")

    def is_member(o: dict[str, Any]) -> bool:
        return "fsd_profile" in o["urn"] or "fsd_company" in o["urn"]

    def pick(group: list[dict[str, Any]], wanted: str, what: str) -> dict[str, Any]:
        matches = [o for o in group if o["name"].casefold() == wanted.casefold()]
        if len(matches) != 1:
            raise ArticleError(
                f"No single {what} named {wanted!r}; the editor offers "
                f"{[o['name'] for o in group]}."
            )
        return matches[0]

    async def select(option: dict[str, Any]) -> list[dict[str, Any]]:
        if not option["checked"]:
            if not await page.evaluate(_CLICK_ENTITY, option["id"]):
                raise ArticleError(f"Could not click {option['name']!r}.")
            await _pause()
        fresh = await page.evaluate(_ENTITY_OPTIONS, ENTITY_RADIO)
        if not any(o["id"] == option["id"] and o["checked"] for o in fresh):
            raise ArticleError(f"{option['name']!r} did not stay selected.")
        return fresh

    members = [o for o in options if is_member(o)]
    if author is not None:
        options = await select(pick(members, author, "author"))
        members = [o for o in options if is_member(o)]
    places = [o for o in options if not is_member(o)]
    if publish_in is None:
        # A standalone article is the destination with no URN.
        standalone = [o for o in places if not o["urn"]]
        if len(standalone) != 1:
            raise ArticleError("The editor offers no single standalone-article option.")
        options = await select(standalone[0])
    else:
        options = await select(pick(places, publish_in, "newsletter"))
    await page.keyboard.press("Escape")
    as_option = next(o for o in options if is_member(o) and o["checked"])
    in_option = next(o for o in options if not is_member(o) and o["checked"])
    return {
        "author": as_option["name"],
        "published_in": in_option["name"] if in_option["urn"] else "standalone article",
    }


async def _read_entities(page: Any) -> dict[str, str]:
    await _open_entity_menu(page)
    options = await page.evaluate(_ENTITY_OPTIONS, ENTITY_RADIO)
    await page.keyboard.press("Escape")
    member = [
        o
        for o in options
        if o["checked"] and ("fsd_profile" in o["urn"] or "fsd_company" in o["urn"])
    ]
    place = [o for o in options if o["checked"] and o not in member]
    if len(member) != 1 or len(place) != 1:
        raise ArticleError("Could not read where the draft would be published.")
    return {
        "author": member[0]["name"],
        "published_in": place[0]["name"] if place[0]["urn"] else "standalone article",
    }


async def _wait_for_draft(page: Any) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30.0
    while loop.time() < deadline:
        if m := _DRAFT_URL.search(page.url):
            return m.group(1)
        await asyncio.sleep(1.0)
    raise ArticleError("LinkedIn did not save a draft (no /article/edit/<id>/ URL).")


async def _publish(page: Any, commentary: str | None) -> dict[str, Any]:
    next_button = await _button_by_text(page, NEXT_BUTTON_TEXT, outside_dialogs=True)
    await next_button.click()
    dialog = page.locator(SHARE_DIALOG)
    await dialog.wait_for(state="visible", timeout=20_000)
    if commentary:
        box = dialog.locator("[role=textbox][contenteditable=true]").first
        await box.click()
        await page.keyboard.insert_text(commentary)
        await _pause()
    await dialog.locator("button").last.click()
    try:
        await dialog.wait_for(state="hidden", timeout=30_000)
    except Exception:
        return {"status": "unconfirmed", "url": page.url}
    await asyncio.sleep(3.0)
    published = "/pulse/" in page.url
    return {"status": "published" if published else "unconfirmed", "url": page.url}


async def create_article(
    page: Any,
    *,
    title: str,
    markdown: str,
    images_dir: str | None,
    cover_image: str | None,
    author: str | None,
    publish_in: str | None,
    publish: bool,
    commentary: str | None,
) -> dict[str, Any]:
    title = " ".join(title.split())
    if not title or len(title) > TITLE_MAX:
        raise ValueError(f"title must be 1-{TITLE_MAX} characters")
    plan = plan_article(markdown, images_dir or ".")
    cover = None
    if cover_image:
        cover = pathlib.Path(cover_image).expanduser().resolve()
        if not cover.is_file():
            raise ValueError(f"cover_image {cover_image!r} does not exist")
    payloads = {id(b): _image_payload(b) for b in plan.images}

    await page.goto(
        NEW_ARTICLE_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT
    )
    await page.locator(BODY).wait_for(state="visible", timeout=30_000)
    await _pause(1.5, 3.0)

    entities = await _choose_entities(page, author, publish_in)
    await _pause()
    if cover:
        await _set_cover(page, cover)
        await _pause()

    await page.locator(TITLE).fill(title)
    await _pause()
    uploaded = 0
    for block in plan.blocks:
        if isinstance(block, HtmlBlock):
            await page.evaluate(_PASTE_HTML, [BODY, block.html])
        else:
            await page.evaluate(_PASTE_IMAGE, [BODY, *payloads[id(block)]])
            uploaded += 1
            await _wait_for_figure(page, uploaded)
            if block.alt:
                await page.evaluate(_SET_CAPTION, [BODY, uploaded - 1, block.alt])
        await _pause()

    if await page.locator(TITLE).input_value() != title:
        raise ArticleError("The title field did not keep the title.")
    draft_id = await _wait_for_draft(page)
    draft_url = f"https://www.linkedin.com/article/edit/{draft_id}/"
    saved = await _check_saved_draft(page, draft_url)
    warnings = list(plan.warnings)
    if saved["images_uploaded"] < uploaded:
        warnings.append(
            f"Only {saved['images_uploaded']} of {uploaded} images were stored "
            "by LinkedIn; check the draft before publishing."
        )
    result: dict[str, Any] = {
        "draft_id": draft_id,
        "draft_url": draft_url,
        "title": title,
        "cover": bool(cover),
        **entities,
        "saved": saved,
        "warnings": warnings,
    }
    if not publish:
        result.update(
            status="draft",
            message="Saved as a draft, not published. Review it at draft_url, "
            "then publish it with salesnav_publish_article(draft_id) or by hand.",
        )
        return result
    # The draft was reopened: check where it would go before it goes there.
    current = await _read_entities(page)
    if current != entities:
        raise ArticleError(
            f"The saved draft would publish as {current}, not {entities}; "
            "not publishing. Review it at draft_url."
        )
    result.update(await _publish(page, commentary))
    return result


async def publish_article(
    page: Any, draft_id: str, *, commentary: str | None, confirm_publish: bool
) -> dict[str, Any]:
    """Publish a reviewed draft, or report where it would go."""
    if not draft_id.isdigit():
        raise ValueError("draft_id must be the number from draft_url")
    draft_url = f"https://www.linkedin.com/article/edit/{draft_id}/"
    await page.goto(draft_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    await page.locator(BODY).wait_for(state="visible", timeout=30_000)
    await _pause(2.0, 3.0)
    title = await page.locator(TITLE).input_value()
    if not title.strip():
        raise ArticleError(
            f"Draft {draft_id} has no title, or is not a draft of this account."
        )
    result: dict[str, Any] = {
        "draft_id": draft_id,
        "draft_url": draft_url,
        "title": title,
        **await _read_entities(page),
        "saved": await page.evaluate(_SAVED_SUMMARY, BODY),
    }
    if not confirm_publish:
        result.update(
            status="not_published",
            message="Nothing was published. Check author and published_in, "
            "then call again with confirm_publish=true.",
        )
        return result
    result.update(await _publish(page, commentary))
    return result
