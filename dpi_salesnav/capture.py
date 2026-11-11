"""Passive capture of the Sales Navigator JSON the page itself requests.

Same rule as upstream's ``conversations.py`` listener: we only read responses
the page load already asked for, never issue our own API requests.
"""

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SALES_API_PREFIX = "/sales-api/"
# LinkedIn's human-verification (HIP / captcha) and checkpoint endpoints.
CHALLENGE_MARKERS = ("/help/api/hip/", "/checkpoint/challenge")


def is_sales_api_response(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "www.linkedin.com" and parsed.path.startswith(
        SALES_API_PREFIX
    )


class SalesApiRecorder:
    """Keep every ``/sales-api/`` JSON body a page load receives, in order."""

    _DRAIN_TIMEOUT = 3.0

    def __init__(self, page: Any):
        self._page = page
        self._pending: list[asyncio.Task[None]] = []
        self.records: list[dict[str, Any]] = []
        # Every response the page gets, any host, for diagnosing a load that
        # never brought the data (host + path + status only).
        self.seen: list[str] = []
        # Set when LinkedIn starts a human-verification check on this page.
        self.challenge: str | None = None
        # Requests the context started and has not finished, for a load that
        # stays on the splash: what it is still waiting for.
        self._inflight: dict[int, tuple[str, str, float]] = {}

    # On the browser context, not the page: Sales Navigator can fetch its data
    # through a service worker, and those responses are only reported there.
    def install(self) -> None:
        context = self._page.context
        context.on("response", self._handle_response)
        context.on("request", self._request_started)
        context.on("requestfinished", self._request_ended)
        context.on("requestfailed", self._request_ended)

    def remove(self) -> None:
        context = self._page.context
        for event, handler in (
            ("response", self._handle_response),
            ("request", self._request_started),
            ("requestfinished", self._request_ended),
            ("requestfailed", self._request_ended),
        ):
            try:
                context.remove_listener(event, handler)
            except Exception:
                logger.debug("Failed to remove %s listener", event, exc_info=True)

    def _request_started(self, request: Any) -> None:
        if len(self._inflight) >= 400:
            return
        parsed = urlparse(request.url)
        self._inflight[id(request)] = (
            f"{parsed.netloc}{parsed.path[:100]}",
            getattr(request, "resource_type", ""),
            time.monotonic(),
        )

    def _request_ended(self, request: Any) -> None:
        self._inflight.pop(id(request), None)

    def pending_requests(self, limit: int = 15) -> list[dict[str, Any]]:
        """The oldest requests still open, host and path only."""
        now = time.monotonic()
        oldest = sorted(self._inflight.values(), key=lambda r: r[2])[:limit]
        return [
            {"url": url, "type": kind, "age_s": round(now - started, 1)}
            for url, kind, started in oldest
        ]

    def _handle_response(self, response: Any) -> None:
        parsed = urlparse(response.url)
        if len(self.seen) < 400:
            self.seen.append(f"{parsed.netloc}{parsed.path[:80]}={response.status}")
        if any(marker in parsed.path for marker in CHALLENGE_MARKERS):
            self.challenge = parsed.path
        if not is_sales_api_response(response.url) or not self._ours(response):
            return

        async def _read() -> None:
            try:
                body = await response.body()
                payload = json.loads(body)
            except Exception:
                payload = None
            self.records.append(
                {
                    "url": response.url,
                    "method": response.request.method,
                    "status": response.status,
                    "json": payload,
                }
            )

        self._pending.append(asyncio.create_task(_read()))

    def _ours(self, response: Any) -> bool:
        """This page's own responses, or ones with no page (service worker)."""
        try:
            return response.frame.page == self._page
        except Exception:
            return True

    async def drain(self) -> None:
        pending, self._pending = self._pending, []
        if not pending:
            return
        _, late = await asyncio.wait(pending, timeout=self._DRAIN_TIMEOUT)
        for task in late:
            task.cancel()

    def matching(self, path_fragment: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.records
            if path_fragment in urlparse(r["url"]).path and r["json"] is not None
        ]
