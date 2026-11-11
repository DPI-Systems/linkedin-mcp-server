"""Keep what the browser showed when a Sales Navigator tool failed.

Every failure of a browser tool writes one artifact directory: a screenshot of
the viewport and of the whole page, the page HTML, its URL, the exception with
its traceback, and the step the tool was at. It is always on, unlike upstream's
``debug_trace``, because the failures this is for do not repeat on demand.

The client then gets the step, the exception and its message, and the artifact
id, instead of FastMCP's masked "Error calling tool". ``get_failure_artifact``
returns an artifact; on dpi-lab it is also a plain directory under
``/opt/dpi/data/linkedin-mcp/cuentaN/failures/``.

A tool says where it is with :func:`step`. A tool that cleans up before
re-raising (the InMail composer closes itself) calls :func:`snapshot` first,
so the screenshot shows the page as it failed rather than as it was left.
"""

import asyncio
import contextvars
import datetime as dt
import functools
import inspect
import json
import logging
import os
import pathlib
import secrets
import shutil
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp.exceptions import ToolError

from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.core.proxy_errors import redact_proxy_credentials
from linkedin_mcp_server.error_handler import (
    CodedToolError,
    ErrorCode,
    raise_tool_error,
)

logger = logging.getLogger(__name__)

FAILURES_DIR_ENV = "DPI_FAILURE_ARTIFACTS_DIR"
DEFAULT_FAILURES_DIR = "/home/pwuser/.linkedin-mcp/failures"
KEEP = 150
_CAPTURE_TIMEOUT = 15.0
_MESSAGE_MAX = 1500
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


def failures_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get(FAILURES_DIR_ENV) or DEFAULT_FAILURES_DIR)


@dataclass
class _Call:
    tool: str
    arguments: dict[str, Any]
    step: str = "start"
    page: Any = None
    artifact: pathlib.Path | None = None
    steps: list[str] = field(default_factory=list)


_CALL: contextvars.ContextVar[_Call | None] = contextvars.ContextVar(
    "dpi_failure_call", default=None
)


def step(name: str) -> None:
    """Record the step the current tool call is at."""
    call = _CALL.get()
    if call is not None:
        call.step = name
        call.steps.append(name)


def use_page(page: Any) -> None:
    """Name the page a failure of the current call should be captured from."""
    call = _CALL.get()
    if call is not None:
        call.page = page


def _new_id(tool: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{tool}-{secrets.token_hex(3)}"


def _prune(root: pathlib.Path) -> None:
    try:
        runs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return
    for old in runs[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


async def _write_page(page: Any, target: pathlib.Path) -> dict[str, Any]:
    """Screenshots, HTML and URL; each part may fail on its own."""
    found: dict[str, Any] = {}
    try:
        found["url"] = page.url
    except Exception as e:
        found["url_error"] = repr(e)[:200]
    for name, full in (("viewport.jpg", False), ("full_page.jpg", True)):
        try:
            await asyncio.wait_for(
                page.screenshot(
                    path=str(target / name), full_page=full, type="jpeg", quality=70
                ),
                _CAPTURE_TIMEOUT,
            )
            found.setdefault("files", []).append(name)
        except Exception as e:
            found[f"{name}_error"] = repr(e)[:200]
    try:
        html = await asyncio.wait_for(page.content(), _CAPTURE_TIMEOUT)
        (target / "page.html").write_text(html, encoding="utf-8")
        found.setdefault("files", []).append("page.html")
    except Exception as e:
        found["html_error"] = repr(e)[:200]
    return found


async def snapshot(page: Any = None) -> None:
    """Capture the page now, before a tool's own cleanup changes it.

    Only once per call: the first capture is the one closest to the failure.
    """
    call = _CALL.get()
    if call is None or call.artifact is not None:
        return
    page = page or call.page
    if page is None:
        return
    try:
        root = failures_dir()
        target = root / _new_id(call.tool)
        target.mkdir(parents=True, mode=0o700)
        call.artifact = target
        found = await _write_page(page, target)
        found["captured_at_step"] = call.step
        (target / "page.json").write_text(json.dumps(found, indent=2))
    except Exception:
        logger.warning("Could not capture the failing page", exc_info=True)


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 300:
        return f"{value[:300]}… ({len(value)} chars)"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value][:20]
    return repr(value)[:300]


async def _record(call: _Call, exc: BaseException) -> str | None:
    """Write the artifact for *exc*; return its id, or None if nothing was kept."""
    await snapshot()
    if call.artifact is None:
        return None
    page_info: dict[str, Any] = {}
    try:
        page_info = json.loads((call.artifact / "page.json").read_text())
    except Exception:
        pass
    info = {
        "id": call.artifact.name,
        "tool": call.tool,
        "step": call.step,
        "steps": call.steps,
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "url": page_info.get("url"),
        "exception_type": type(exc).__name__,
        "exception_message": redact_proxy_credentials(str(exc)),
        "traceback": redact_proxy_credentials("".join(traceback.format_exception(exc))),
        "arguments": {k: _short(v) for k, v in call.arguments.items()},
        "page": page_info,
    }
    try:
        (call.artifact / "failure.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False)
        )
    except Exception:
        logger.warning("Could not write failure.json", exc_info=True)
    _prune(call.artifact.parent)
    logger.warning(
        "%s failed at step %s (%s: %s); artifact %s",
        call.tool,
        call.step,
        type(exc).__name__,
        str(exc)[:300],
        call.artifact,
    )
    return call.artifact.name


def _code_for(exc: BaseException) -> ErrorCode:
    # The page was not in the state the tool needed: a missing button, a
    # composer that never opened, a wait that ran out.
    if type(exc).__name__ in {"SendRefused", "TimeoutError"}:
        return ErrorCode.PAGE_UNREADABLE
    return ErrorCode.INTERNAL


def _failure_text(call: _Call, exc: BaseException, summary: str, artifact: str | None):
    detail = {
        "tool": call.tool,
        "step": call.step,
        "exception_type": type(exc).__name__,
        "exception_message": redact_proxy_credentials(str(exc))[:_MESSAGE_MAX],
        "failure_artifact": artifact,
    }
    where = f'{call.tool} failed at step "{call.step}"'
    tail = (
        f"Artifact {artifact}: get_failure_artifact(artifact_id={artifact!r})."
        if artifact
        else "No artifact was kept (the browser page was not open)."
    )
    return f"{where}: {summary}\n{tail}\n\n{json.dumps(detail, ensure_ascii=False)}"


def with_failure_artifacts[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Capture the page and name the step when a browser tool fails.

    Goes under ``@tool_errors``. A login failure passes through untouched, so
    upstream can start its re-login. A failure upstream already shapes keeps
    its code and message, with the step and artifact appended. Anything else,
    which upstream would mask, becomes a coded error naming its type and
    message.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind_partial(*args, **kwargs).arguments
        call = _Call(
            tool=getattr(fn, "__name__", "tool"),
            arguments={k: v for k, v in bound.items() if k != "ctx"},
        )
        token = _CALL.set(call)
        try:
            return await fn(*args, **kwargs)
        except AuthenticationError:
            raise
        except Exception as exc:
            if call.page is None:
                # Refused before the browser was touched (bad arguments, a
                # download that failed): nothing to capture.
                raise
            artifact = await _record(call, exc)
            try:
                raise_tool_error(exc, call.tool)
            except CodedToolError as shaped:
                raise CodedToolError(
                    _failure_text(call, exc, str(shaped), artifact),
                    shaped.code,
                    retry_after=shaped.retry_after,
                ) from (shaped.__cause__ or shaped)
            except ToolError as shaped:
                raise CodedToolError(
                    _failure_text(call, exc, str(shaped), artifact),
                    _code_for(exc),
                ) from exc
            except Exception:
                summary = (
                    f"{type(exc).__name__}: "
                    f"{redact_proxy_credentials(str(exc))[:_MESSAGE_MAX]}"
                )
                raise CodedToolError(
                    _failure_text(call, exc, summary, artifact), _code_for(exc)
                ) from exc
        finally:
            _CALL.reset(token)

    return wrapper


def _artifact_path(artifact_id: str) -> pathlib.Path:
    if not artifact_id or not set(artifact_id) <= _ID_CHARS:
        raise CodedToolError(
            f"Not an artifact id: {artifact_id!r}", ErrorCode.INVALID_ARGUMENT
        )
    path = failures_dir() / artifact_id
    if not path.is_dir():
        raise CodedToolError(
            f"No failure artifact {artifact_id!r} on this server.",
            ErrorCode.NOT_FOUND,
        )
    return path


def list_artifacts(limit: int = 20) -> list[dict[str, Any]]:
    root = failures_dir()
    try:
        runs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return []
    out = []
    for run in runs[:limit]:
        try:
            info = json.loads((run / "failure.json").read_text())
        except Exception:
            info = {}
        out.append(
            {
                "id": run.name,
                "tool": info.get("tool"),
                "step": info.get("step"),
                "at": info.get("at"),
                "exception_type": info.get("exception_type"),
                "exception_message": (info.get("exception_message") or "")[:200],
                "url": info.get("url"),
            }
        )
    return out


def read_artifact(artifact_id: str) -> tuple[dict[str, Any], pathlib.Path]:
    path = _artifact_path(artifact_id)
    try:
        info = json.loads((path / "failure.json").read_text())
    except Exception:
        info = {"id": artifact_id}
    info["files"] = sorted(p.name for p in path.iterdir())
    info["path"] = str(path)
    return info, path
