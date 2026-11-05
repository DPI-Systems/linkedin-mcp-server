"""The LinkedIn layer, reached through the MCP server.

The MCP server owns the browser and the logged-in profile, and serializes
every tool call. The backend is one more client of it, so the API and an LLM
can use the same account without opening the profile twice.
"""

from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.core.config import get_settings

ERROR_META_KEY = "linkedin.dev/error"
# LinkedIn refused or wants a person: every automation stops on these.
STOP_CODES = frozenset({"auth_required", "account_restricted", "rate_limited"})


class LinkedInError(RuntimeError):
    def __init__(self, tool: str, code: str, message: str) -> None:
        super().__init__(f"{tool}: {code}: {message}")
        self.tool = tool
        self.code = code
        self.message = message

    @property
    def must_stop(self) -> bool:
        return self.code in STOP_CODES


def _error_from(tool: str, result: Any) -> LinkedInError:
    meta = getattr(result, "meta", None) or {}
    code = (meta.get(ERROR_META_KEY) or {}).get("code", "internal")
    text = " ".join(getattr(b, "text", "") for b in result.content or []).strip()
    return LinkedInError(tool, code, text or "tool failed")


class LinkedInClient:
    def __init__(self, url: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.mcp_url
        self.timeout = timeout or settings.mcp_timeout_seconds
        self._token = settings.mcp_token

    def _client(self, timeout: float) -> Client:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        return Client(StreamableHttpTransport(self.url, headers=headers), timeout=timeout)

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        async with self._client(self.timeout) as client:
            result = await client.call_tool(tool, args, raise_on_error=False)
        if result.is_error:
            raise _error_from(tool, result)
        data = result.structured_content
        if not isinstance(data, dict):
            raise LinkedInError(tool, "internal", "no structured result")
        return data

    async def reachable(self) -> bool:
        try:
            # This server does not answer MCP ping; listing tools is as cheap
            # and never touches the browser.
            async with self._client(10) as client:
                await client.list_tools()
            return True
        except Exception:
            return False

    async def network_updates(self, categories: list[str] | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"max_results": 50}
        if categories:
            args["categories"] = categories
        return await self.call("linkedin_network_updates", args)

    async def person_profile(self, username: str) -> dict[str, Any]:
        # main_profile comes with every call; experience gives the employer
        # and since when.
        return await self.call(
            "get_person_profile",
            {"linkedin_username": username, "sections": "experience"},
        )

    async def contact_info(self, username: str) -> dict[str, Any]:
        return await self.call("linkedin_contact_info", {"linkedin_username": username})

    async def search_companies(self, name: str) -> dict[str, Any]:
        return await self.call("salesnav_search_companies", {"name": name})

    async def company_people(
        self, company_id: str, max_results: int, filters: dict[str, Any]
    ) -> dict[str, Any]:
        args = {"company_id": company_id, "max_results": max_results}
        args.update({k: v for k, v in filters.items() if v not in (None, [], "", False)})
        return await self.call("salesnav_company_people", args)
