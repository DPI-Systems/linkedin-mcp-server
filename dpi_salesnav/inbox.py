"""Sales Navigator inbox: thread list and one thread's messages.

Read from the ``salesApiMessagingThreads`` JSON the inbox page loads itself.
Opening the inbox also opens its newest thread, and opening a thread marks it
read, exactly as when a person does it.
"""

import asyncio
import datetime as dt
import logging
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

from dpi_salesnav.capture import SalesApiRecorder
from dpi_salesnav.people import _raise_if_challenged, _raise_if_throttled

logger = logging.getLogger(__name__)

INBOX_URL = "https://www.linkedin.com/sales/inbox"
THREADS_PATH = "/sales-api/salesApiMessagingThreads"
NAV_CHROME_PATH = "/sales-api/salesApiNavChrome"
_RESPONSE_TIMEOUT = 20.0
_THREAD_ID = re.compile(r"^2-[A-Za-z0-9+/=_-]+$")
_PROFILE = re.compile(r"urn:li:fs_salesProfile:\(([^,]+),\s*([^,]*),\s*([^)]*)\)")
# Types the other side sends; ours are MESSAGE and INMAIL.
_REPLY_TYPES = {"INMAIL_REPLY", "INMAIL_ACCEPT", "INMAIL_DECLINE"}


def validate_thread_id(thread_id: str) -> str:
    thread_id = unquote(thread_id.strip())
    if not _THREAD_ID.match(thread_id):
        raise ValueError(f"Not a Sales Navigator thread id: {thread_id!r}")
    return thread_id


def _profile_id(urn: str | None) -> str | None:
    m = _PROFILE.match(urn or "")
    return m.group(1) if m else None


def _lead_url(urn: str | None) -> str | None:
    m = _PROFILE.match(urn or "")
    if not m or not m.group(2).strip() or not m.group(3).strip():
        return None
    return "https://www.linkedin.com/sales/lead/{},{},{}".format(*m.groups())


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).isoformat()


def viewer_profile_id(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        body = record.get("json")
        if (
            NAV_CHROME_PATH in record["url"]
            and isinstance(body, dict)
            and isinstance(body.get("member"), str)
        ):
            return _profile_id(body["member"])
    return None


def _people(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    people = {}
    for item in payload.get("included") or []:
        pid = _profile_id(item.get("entityUrn")) if isinstance(item, dict) else None
        if pid:
            people[pid] = item
    return people


def _message(msg: dict[str, Any], me: str | None) -> dict[str, Any]:
    author = _profile_id(msg.get("author"))
    return {
        "id": msg.get("id"),
        "type": msg.get("type"),
        "from_me": author is not None and author == me,
        "subject": msg.get("subject"),
        "body": msg.get("body"),
        "delivered_at": _iso(msg.get("deliveredAt")),
        "attachments": [a.get("name") for a in msg.get("attachments") or []],
    }


def thread_from_payload(
    thread: dict[str, Any], people: dict[str, dict[str, Any]], me: str | None
) -> dict[str, Any]:
    others = [u for u in thread.get("participants") or [] if _profile_id(u) != me]
    contact_urn = others[0] if others else None
    person = people.get(_profile_id(contact_urn) or "", {})
    messages = sorted(
        (_message(m, me) for m in thread.get("messages") or [] if isinstance(m, dict)),
        key=lambda m: m["delivered_at"] or "",
    )
    last = messages[-1] if messages else None
    return {
        "thread_id": thread.get("id"),
        "contact": {
            "full_name": person.get("fullName"),
            "degree": person.get("degree"),
            "member_urn": person.get("objectUrn"),
            "sales_nav_url": _lead_url(person.get("entityUrn") or contact_urn),
        },
        "unread_count": thread.get("unreadMessageCount"),
        "total_messages": thread.get("totalMessageCount"),
        "archived": thread.get("archived"),
        "last_message": last,
        "awaiting_their_reply": bool(last and last["from_me"]),
        "they_replied": any(
            (me is not None and not m["from_me"]) or m["type"] in _REPLY_TYPES
            for m in messages
        ),
        "messages": messages,
        "url": f"{INBOX_URL}/{thread.get('id')}" if thread.get("id") else None,
    }


async def _load(page: Any, url: str, want: Any) -> tuple[Any, list[dict[str, Any]]]:
    recorder = SalesApiRecorder(page)
    recorder.install()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESPONSE_TIMEOUT
        while loop.time() < deadline:
            await recorder.drain()
            found = [r for r in recorder.records if r["json"] is not None and want(r)]
            if found:
                # The viewer's identity arrives with the page chrome; give it
                # a moment if it has not come yet.
                if viewer_profile_id(recorder.records) is None:
                    await asyncio.sleep(1.5)
                    await recorder.drain()
                return found[-1]["json"], list(recorder.records)
            _raise_if_throttled()
            await asyncio.sleep(0.5)
        _raise_if_challenged(recorder)
        return None, list(recorder.records)
    finally:
        recorder.remove()
        await recorder.drain()


def _is_thread_list(record: dict[str, Any]) -> bool:
    return urlparse(record["url"]).path == THREADS_PATH and record["method"] == "GET"


# Older threads load as the thread list is scrolled, so the inbox is paged by
# scrolling it, like a person, and keeping every page Sales Navigator fetches.
# Every scrollable box goes to its bottom: the list is one of them, and the open
# thread already sits at its bottom (its older messages load scrolling *up*).
_SCROLL_JS = """() => {
  let n = 0;
  for (const el of document.querySelectorAll('*')) {
    const oy = getComputedStyle(el).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 4) {
      el.scrollTop = el.scrollHeight;
      el.dispatchEvent(new Event('scroll', {bubbles: true}));
      n++;
    }
  }
  window.scrollTo(0, document.body.scrollHeight);
  return n;
}"""
_PAGE_WAIT = 8.0  # seconds for an older page to arrive after a scroll
_SCROLL_PAUSE = 1.5  # between scrolls, so paging stays at a person's pace
_MAX_EMPTY_SCROLLS = 2  # scrolls with no new thread before calling it the end
_MAX_PAGES = 100


def merge_thread_pages(
    payloads: list[dict[str, Any]], me: str | None, limit: int
) -> list[dict[str, Any]]:
    """Threads of every list page, newest first, each once (pages overlap)."""
    people: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        people.update(_people(payload))
    seen: set[str] = set()
    threads = []
    for payload in payloads:
        for t in (payload.get("data") or {}).get("elements") or []:
            if not isinstance(t, dict) or t.get("id") in seen:
                continue
            seen.add(t.get("id"))
            thread = thread_from_payload(t, people, me)
            # The list carries only the newest message of each thread.
            thread.pop("messages")
            threads.append(thread)
    return threads[:limit]


def _list_payloads(recorder: SalesApiRecorder) -> list[dict[str, Any]]:
    return [
        r["json"]
        for r in recorder.records
        if _is_thread_list(r) and isinstance(r["json"], dict)
    ]


def _thread_ids(payloads: list[dict[str, Any]]) -> set[str]:
    return {
        t.get("id")
        for p in payloads
        for t in (p.get("data") or {}).get("elements") or []
        if isinstance(t, dict)
    }


async def read_inbox(page: Any, *, limit: int = 20) -> dict[str, Any]:
    recorder = SalesApiRecorder(page)
    recorder.install()
    reached_end = False
    stopped = None
    try:
        await page.goto(INBOX_URL, wait_until="domcontentloaded")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESPONSE_TIMEOUT
        while loop.time() < deadline:
            await recorder.drain()
            if _list_payloads(recorder):
                break
            _raise_if_throttled()
            await asyncio.sleep(0.5)
        else:
            _raise_if_challenged(recorder)
            raise RuntimeError(
                f"Sales Navigator inbox loaded no thread data; page ended at {page.url}"
            )
        if viewer_profile_id(recorder.records) is None:
            await asyncio.sleep(1.5)
            await recorder.drain()

        empty_scrolls = 0
        while len(_thread_ids(_list_payloads(recorder))) < limit:
            if len(_list_payloads(recorder)) >= _MAX_PAGES:
                break
            known = len(_thread_ids(_list_payloads(recorder)))
            await asyncio.sleep(_SCROLL_PAUSE)
            await page.evaluate(_SCROLL_JS)
            deadline = loop.time() + _PAGE_WAIT
            grew = False
            while loop.time() < deadline:
                await recorder.drain()
                _raise_if_throttled()
                if len(_thread_ids(_list_payloads(recorder))) > known:
                    grew = True
                    break
                await asyncio.sleep(0.5)
            if grew:
                empty_scrolls = 0
                continue
            if recorder.challenge:
                # A check while paging: keep what was read, page no further.
                stopped = f"LinkedIn ran a check ({recorder.challenge})"
                break
            empty_scrolls += 1
            if empty_scrolls >= _MAX_EMPTY_SCROLLS:
                reached_end = True
                break
    finally:
        recorder.remove()
        await recorder.drain()

    payloads = _list_payloads(recorder)
    me = viewer_profile_id(recorder.records)
    threads = merge_thread_pages(payloads, me, limit)
    return {
        "viewer_identified": me is not None,
        "count": len(threads),
        "pages_loaded": len(payloads),
        # True when scrolling brought no older thread: the list ends here.
        "reached_end": reached_end,
        # Why paging stopped before the end, if it did.
        "stopped": stopped,
        "threads": threads,
    }


async def read_thread(page: Any, thread_id: str) -> dict[str, Any]:
    thread_id = validate_thread_id(thread_id)

    def is_this_thread(record: dict[str, Any]) -> bool:
        path = unquote(urlparse(record["url"]).path)
        return path == f"{THREADS_PATH}/{thread_id}" and record["method"] == "GET"

    url = f"{INBOX_URL}/{quote(thread_id, safe='=')}"
    payload, records = await _load(page, url, is_this_thread)
    _raise_if_throttled()
    if payload is None:
        raise RuntimeError(
            f"Sales Navigator loaded no data for thread {thread_id}; "
            f"page ended at {page.url}"
        )
    me = viewer_profile_id(records)
    people = _people(payload)
    for record in records:
        # The thread payload may not include the contact; the list does.
        if _is_thread_list(record) and isinstance(record["json"], dict):
            people = {**_people(record["json"]), **people}
    thread = thread_from_payload(payload.get("data") or {}, people, me)
    thread["viewer_identified"] = me is not None
    return thread
