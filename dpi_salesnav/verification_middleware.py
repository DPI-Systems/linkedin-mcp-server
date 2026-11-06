"""Turn a LinkedIn check into ``verification_required``, with the viewer open.

Two jobs, both at the tool-call boundary so they cover upstream's tools too:

* A call that fails with ``account_restricted`` or ``auth_required`` opens the
  verification viewer and fails again with a message that starts
  ``verification_required`` and carries the viewer URL. A caller can tell that
  apart from a broken browser, whose usual fix (``close_session``) would close
  the very window the person needs.
* While the viewer is open, every other tool is refused, ``close_session``
  included: a navigation would take the page away from the person solving the
  check, and a close would remove it.

Added after upstream's middleware, which makes it the innermost: it sees the
coded exception before ``ErrorCodeMiddleware`` turns it into a result, and it
runs inside the sequential lock like any tool.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from linkedin_mcp_server.error_handler import ERROR_META_KEY, CodedToolError, ErrorCode

from dpi_salesnav import verification

logger = logging.getLogger(__name__)

VERIFICATION_TOOLS = frozenset(
    {
        "linkedin_verification_open",
        "linkedin_verification_status",
        "linkedin_verification_finish",
    }
)
_CHECK_CODES = frozenset({ErrorCode.ACCOUNT_RESTRICTED, ErrorCode.AUTH_REQUIRED})
_NEXT_STEPS = (
    "Give this URL to the user and wait for them to solve it there; do not "
    "solve it yourself, do not retry the call and do not call close_session. "
    "Call linkedin_verification_status(wait_seconds=...) to wait without "
    "loading anything, then linkedin_verification_finish, then repeat the call."
)


def _required(code: ErrorCode, detail: str, state: dict[str, Any]) -> CodedToolError:
    return CodedToolError(
        f"verification_required: {detail} Viewer: {state.get('viewer_url')} "
        f"(page state {state.get('state')!r}, closes in "
        f"{state.get('expires_in_seconds')} s). {_NEXT_STEPS}",
        code,
    )


class VerificationMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        name = getattr(context.message, "name", "")
        if name in VERIFICATION_TOOLS:
            return await call_next(context)
        if verification.is_open():
            raise _required(
                ErrorCode.ACCOUNT_RESTRICTED,
                f"{name} was not run because a person is solving a LinkedIn "
                "check in the server's browser.",
                await verification.verification_status(),
            )
        try:
            result = await call_next(context)
        except CodedToolError as exc:
            if exc.code not in _CHECK_CODES:
                raise
            replacement = await _opened(name, exc.code, str(exc), exc)
            if replacement is exc:
                raise
            raise replacement from exc
        # Some tools return their failure as a result instead of raising it.
        code = _result_code(result)
        if code in _CHECK_CODES:
            raise await _opened(name, code, _result_text(result), None)
        return result


async def _opened(
    name: str, code: ErrorCode, detail: str, original: Exception | None
) -> Exception:
    """The verification_required error, or the original when no viewer opens."""
    try:
        state = await verification.open_verification()
    except Exception:
        logger.warning(
            "Could not open the verification viewer after %s", name, exc_info=True
        )
        return original or CodedToolError(detail, code)
    return _required(code, detail, state)


def _result_code(result: Any) -> ErrorCode | None:
    marker = (getattr(result, "meta", None) or {}).get(ERROR_META_KEY) or {}
    try:
        return ErrorCode(marker.get("code"))
    except ValueError:
        return None


def _result_text(result: Any) -> str:
    return " ".join(
        getattr(block, "text", "") for block in getattr(result, "content", None) or []
    ).strip()
