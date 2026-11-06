"""verification_required: the viewer opens on a LinkedIn check, and holds the browser."""

from types import SimpleNamespace

import pytest
from fastmcp.tools import ToolResult

from linkedin_mcp_server.error_handler import CodedToolError, ErrorCode, error_result

from dpi_salesnav import verification_middleware as vm

OPEN_STATE = {
    "open": True,
    "state": "challenge",
    "viewer_url": "https://verify.example/login.html#token=abc",
    "expires_in_seconds": 1800,
}


@pytest.fixture
def viewer(monkeypatch):
    calls = {"opened": 0, "open": False}

    async def open_verification():
        calls["opened"] += 1
        calls["open"] = True
        return OPEN_STATE

    async def verification_status():
        return OPEN_STATE

    fake = SimpleNamespace(
        open_verification=open_verification,
        verification_status=verification_status,
        is_open=lambda: calls["open"],
    )
    monkeypatch.setattr(vm, "verification", fake)
    return calls


def ctx(name):
    return SimpleNamespace(message=SimpleNamespace(name=name))


def raising(code):
    async def call_next(_):
        raise CodedToolError("LinkedIn showed a check.", code)

    return call_next


async def ok(_):
    return ToolResult(structured_content={"fine": True})


@pytest.mark.parametrize(
    "code", [ErrorCode.ACCOUNT_RESTRICTED, ErrorCode.AUTH_REQUIRED]
)
async def test_a_check_becomes_verification_required_with_the_url(viewer, code):
    with pytest.raises(CodedToolError) as caught:
        await vm.VerificationMiddleware().on_call_tool(
            ctx("salesnav_company_people"), raising(code)
        )
    assert str(caught.value).startswith("verification_required:")
    assert OPEN_STATE["viewer_url"] in str(caught.value)
    assert caught.value.code is code
    assert viewer["opened"] == 1


async def test_a_check_returned_as_a_result_opens_the_viewer_too(viewer):
    async def call_next(_):
        return error_result("restricted", ErrorCode.ACCOUNT_RESTRICTED)

    with pytest.raises(CodedToolError, match="verification_required"):
        await vm.VerificationMiddleware().on_call_tool(ctx("search_people"), call_next)
    assert viewer["opened"] == 1


async def test_other_failures_pass_through_untouched(viewer):
    with pytest.raises(CodedToolError) as caught:
        await vm.VerificationMiddleware().on_call_tool(
            ctx("get_inbox"), raising(ErrorCode.NETWORK)
        )
    assert not str(caught.value).startswith("verification_required")
    assert viewer["opened"] == 0


async def test_while_open_other_tools_and_close_session_are_refused(viewer):
    viewer["open"] = True
    ran = []

    async def call_next(_):
        ran.append(True)
        return await ok(_)

    for name in ("close_session", "salesnav_search_companies"):
        with pytest.raises(CodedToolError, match="verification_required"):
            await vm.VerificationMiddleware().on_call_tool(ctx(name), call_next)
    assert ran == []

    result = await vm.VerificationMiddleware().on_call_tool(
        ctx("linkedin_verification_status"), ok
    )
    assert result.structured_content == {"fine": True}


async def test_when_no_viewer_can_open_the_original_error_stays(monkeypatch, viewer):
    async def broken():
        raise RuntimeError("no display")

    monkeypatch.setattr(vm.verification, "open_verification", broken)
    with pytest.raises(CodedToolError) as caught:
        await vm.VerificationMiddleware().on_call_tool(
            ctx("salesnav_company_people"), raising(ErrorCode.ACCOUNT_RESTRICTED)
        )
    assert str(caught.value) == "LinkedIn showed a check."
