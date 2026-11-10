"""Files to attach to a Sales Navigator message.

The server runs in a container and cannot see the caller's disk, so a file is
named either by an ``https://`` URL, which is downloaded into a temporary
directory, or by a path inside ``ATTACHMENTS_DIR`` on the server. A path
anywhere else is refused: the container also holds the LinkedIn session, and
an attachment is sent to someone outside it.

Sales Navigator shows an attachment by its file name, which nobody translates,
so the name is what the composer and the thread are checked for afterwards.
That is why two files with the same name are refused.
"""

import asyncio
import ipaddress
import os
import pathlib
import re
import socket
from dataclasses import dataclass
from email.message import Message
from urllib.parse import unquote, urljoin, urlparse

import httpx2

MAX_FILES = 5
# LinkedIn messaging takes files up to 20 MB.
MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0
MAX_REDIRECTS = 5
ATTACHMENTS_DIR_ENV = "DPI_INMAIL_ATTACHMENTS_DIR"
DEFAULT_ATTACHMENTS_DIR = "/home/pwuser/.linkedin-mcp/attachments"
_UNSAFE_NAME = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


class AttachmentError(ValueError):
    """An attachment could not be fetched or is not allowed."""


@dataclass(frozen=True)
class Attachment:
    name: str
    path: pathlib.Path
    size: int
    source: str

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "bytes": self.size, "source": self.source}


def attachments_dir() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(ATTACHMENTS_DIR_ENV) or DEFAULT_ATTACHMENTS_DIR
    ).resolve()


def safe_name(raw: str) -> str:
    """A file name Sales Navigator can show: no directories, no control chars."""
    name = _UNSAFE_NAME.sub("_", pathlib.PurePosixPath(raw.strip()).name).strip()
    if not name or name in {".", ".."} or name.startswith("."):
        raise AttachmentError(f"No usable file name in {raw!r}.")
    return name[:200]


def _name_from_headers(headers: httpx2.Headers) -> str | None:
    value = headers.get("content-disposition")
    if not value:
        return None
    message = Message()
    message["content-disposition"] = value
    name = message.get_filename()
    return name or None


def _check_public_host(host: str) -> None:
    """Refuse hosts that resolve to loopback, private or link-local addresses.

    The container sits on the lab network; a URL must not turn it into a way
    of reading internal services and mailing them to a lead.
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise AttachmentError(f"Cannot resolve {host}.") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise AttachmentError(
                f"{host} resolves to a non-public address ({address}); "
                "attachments are only downloaded from public https hosts."
            )


def _check_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AttachmentError(f"Attachment URLs must be https: {url!r}.")
    return parsed.hostname


async def download(
    url: str,
    workdir: pathlib.Path,
    *,
    client: httpx2.AsyncClient | None = None,
    check_host: bool = True,
) -> Attachment:
    own = client is None
    client = client or httpx2.AsyncClient(timeout=DOWNLOAD_TIMEOUT)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = _check_url(current)
            if check_host:
                await asyncio.to_thread(_check_public_host, host)
            async with client.stream("GET", current, follow_redirects=False) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise AttachmentError(f"Redirect without Location: {current}")
                    current = urljoin(current, location)
                    continue
                if r.status_code != 200:
                    raise AttachmentError(
                        f"Downloading {url} returned HTTP {r.status_code}."
                    )
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                    raise AttachmentError(f"{url} is larger than {MAX_BYTES} bytes.")
                name = safe_name(
                    _name_from_headers(r.headers) or unquote(urlparse(current).path)
                )
                target = workdir / name
                size = 0
                with target.open("wb") as out:
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise AttachmentError(
                                f"{url} is larger than {MAX_BYTES} bytes."
                            )
                        out.write(chunk)
                if size == 0:
                    raise AttachmentError(f"{url} is empty.")
                return Attachment(name=name, path=target, size=size, source=url)
        raise AttachmentError(f"Too many redirects for {url}.")
    except httpx2.HTTPError as e:
        raise AttachmentError(f"Downloading {url} failed: {e}") from e
    finally:
        if own:
            await client.aclose()


def local(path: str, root: pathlib.Path | None = None) -> Attachment:
    root = root or attachments_dir()
    candidate = pathlib.Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise AttachmentError(
            f"Local attachments must be inside {root} on the server; "
            f"{path!r} is not. Pass an https URL instead."
        )
    if not resolved.is_file():
        raise AttachmentError(f"No such file on the server: {resolved}.")
    size = resolved.stat().st_size
    if size == 0 or size > MAX_BYTES:
        raise AttachmentError(f"{resolved} must be 1 byte to {MAX_BYTES} bytes.")
    return Attachment(
        name=safe_name(resolved.name), path=resolved, size=size, source=path
    )


async def fetch_all(
    sources: list[str],
    workdir: pathlib.Path,
    *,
    client: httpx2.AsyncClient | None = None,
    check_host: bool = True,
) -> list[Attachment]:
    if len(sources) > MAX_FILES:
        raise AttachmentError(f"At most {MAX_FILES} attachments per message.")
    out: list[Attachment] = []
    for source in sources:
        source = source.strip()
        if "://" in source:
            out.append(
                await download(source, workdir, client=client, check_host=check_host)
            )
        else:
            out.append(local(source))
    names = [a.name for a in out]
    if len(set(names)) != len(names):
        raise AttachmentError(
            f"Attachments need distinct file names, got {names}: the name is "
            "how the attachment is found in the composer and the thread."
        )
    return out
