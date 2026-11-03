"""Passive capture of the Sales Navigator JSON the page itself requests.

Same rule as upstream's ``conversations.py`` listener: we only read responses
the page load already asked for, never issue our own API requests.
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SALES_API_PREFIX = "/sales-api/"


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

    # On the browser context, not the page: Sales Navigator can fetch its data
    # through a service worker, and those responses are only reported there.
    def install(self) -> None:
        self._page.context.on("response", self._handle_response)

    def remove(self) -> None:
        try:
            self._page.context.remove_listener("response", self._handle_response)
        except Exception:
            logger.debug("Failed to remove sales-api listener", exc_info=True)

    def _handle_response(self, response: Any) -> None:
        if len(self.seen) < 400:
            parsed = urlparse(response.url)
            self.seen.append(f"{parsed.netloc}{parsed.path[:80]}={response.status}")
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
