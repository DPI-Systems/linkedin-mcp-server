"""A failed browser tool keeps the page and tells the client what failed."""

import json

import pytest
from fastmcp import Client, FastMCP

from linkedin_mcp_server.core.exceptions import AuthenticationError, RateLimitError
from linkedin_mcp_server.dependencies import tool_errors
from linkedin_mcp_server.error_handler import (
    ERROR_META_KEY,
    CodedToolError,
    ErrorCode,
    ErrorCodeMiddleware,
)

from dpi_salesnav import failures


class FakePage:
    url = "https://www.linkedin.com/sales/lead/X,NAME_SEARCH,y"

    def __init__(self):
        self.shots = []

    async def screenshot(self, *, path, full_page, **_):
        self.shots.append(full_page)
        with open(path, "wb") as f:
            f.write(b"\xff\xd8jpeg")

    async def content(self):
        return "<html><body>composer</body></html>"


@pytest.fixture(autouse=True)
def artifacts_dir(tmp_path, monkeypatch):
    root = tmp_path / "failures"
    monkeypatch.setenv(failures.FAILURES_DIR_ENV, str(root))
    return root


def _tool(fail, page=None):
    @failures.with_failure_artifacts
    async def salesnav_thing(lead_url: str, ctx=None):
        if page is not None:
            failures.use_page(page)
        failures.step("open_composer")
        raise fail

    return salesnav_thing


async def test_an_unclassified_failure_names_step_exception_and_artifact(
    artifacts_dir,
):
    page = FakePage()
    with pytest.raises(CodedToolError) as caught:
        await _tool(TimeoutError("Locator.wait_for: Timeout 15000ms"), page)("u")
    err = caught.value
    assert err.code == ErrorCode.PAGE_UNREADABLE
    detail = json.loads(str(err).rsplit("\n\n", 1)[1])
    assert detail["step"] == "open_composer"
    assert detail["exception_type"] == "TimeoutError"
    assert "Timeout 15000ms" in detail["exception_message"]
    run = artifacts_dir / detail["failure_artifact"]
    assert {p.name for p in run.iterdir()} >= {
        "viewport.jpg",
        "full_page.jpg",
        "page.html",
        "failure.json",
    }
    info = json.loads((run / "failure.json").read_text())
    assert info["url"] == page.url
    assert info["arguments"] == {"lead_url": "u"}
    assert "TimeoutError" in info["traceback"]
    assert sorted(page.shots) == [False, True]


async def test_a_shaped_failure_keeps_its_code_and_gains_the_artifact():
    with pytest.raises(CodedToolError) as caught:
        await _tool(RateLimitError("slow down", suggested_wait_time=60), FakePage())(
            "u"
        )
    assert caught.value.code == ErrorCode.RATE_LIMITED
    assert '"step": "open_composer"' in str(caught.value)
    assert "get_failure_artifact" in str(caught.value)


async def test_login_failures_pass_through_for_the_relogin(artifacts_dir):
    with pytest.raises(AuthenticationError):
        await _tool(AuthenticationError("expired"), FakePage())("u")
    assert not artifacts_dir.exists()


async def test_nothing_is_captured_before_the_browser_is_used(artifacts_dir):
    with pytest.raises(ValueError, match="bad url"):
        await _tool(ValueError("bad url"))("u")
    assert not artifacts_dir.exists()


async def test_a_snapshot_taken_before_cleanup_is_the_one_kept(artifacts_dir):
    page = FakePage()

    @failures.with_failure_artifacts
    async def salesnav_cleans_up():
        failures.use_page(page)
        failures.step("attach")
        try:
            raise RuntimeError("stuck")
        except RuntimeError:
            await failures.snapshot()
            page.content = _after_cleanup
            raise

    async def _after_cleanup():
        return "<html>closed</html>"

    with pytest.raises(CodedToolError):
        await salesnav_cleans_up()
    [run] = list(artifacts_dir.iterdir())
    assert "composer" in (run / "page.html").read_text()
    assert len(page.shots) == 2


async def test_the_client_gets_the_failure_not_a_masked_error(artifacts_dir):
    mcp = FastMCP("t", mask_error_details=True)
    mcp.add_middleware(ErrorCodeMiddleware())
    page = FakePage()

    @mcp.tool
    @tool_errors
    @failures.with_failure_artifacts
    async def salesnav_send_inmail(lead_url: str) -> dict:
        failures.use_page(page)
        failures.step("find_message_button")
        raise KeyError("data-anchor-send-inmail")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "salesnav_send_inmail", {"lead_url": "u"}, raise_on_error=False
        )
    assert result.is_error
    text = result.content[0].text
    assert "Error calling tool" not in text
    assert 'failed at step "find_message_button"' in text
    assert "KeyError" in text
    assert result.meta[ERROR_META_KEY]["code"] == "internal"
    artifact = json.loads(text.rsplit("\n\n", 1)[1])["failure_artifact"]

    info, path = failures.read_artifact(artifact)
    assert info["step"] == "find_message_button"
    assert (path / "viewport.jpg").is_file()
    assert [a["id"] for a in failures.list_artifacts()] == [artifact]


def test_artifact_ids_cannot_leave_the_failures_dir():
    for bad in ("../cookies.json", "a/b", "", "X"):
        with pytest.raises(CodedToolError):
            failures.read_artifact(bad)


def test_old_artifacts_are_pruned(artifacts_dir, monkeypatch):
    monkeypatch.setattr(failures, "KEEP", 2)
    artifacts_dir.mkdir()
    for i in range(4):
        (artifacts_dir / f"2026111{i}-000000-t-aaaaaa").mkdir()
    failures._prune(artifacts_dir)
    assert sorted(p.name for p in artifacts_dir.iterdir()) == [
        "20261112-000000-t-aaaaaa",
        "20261113-000000-t-aaaaaa",
    ]
