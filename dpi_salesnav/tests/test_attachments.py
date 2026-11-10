"""Offline tests for fetching InMail attachments (synthetic data only)."""

import socket

import httpx2
import pytest

from dpi_salesnav import attachments
from dpi_salesnav.attachments import AttachmentError, fetch_all, local

PDF = b"%PDF-1.4\n" + b"0" * 1024


def _client(handler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_download_names_the_file_from_the_url_path(tmp_path):
    def handler(request):
        return httpx2.Response(200, content=PDF)

    url = "https://files.example/x/Licitaciones%20con%20IA%20-%20Dossier.pdf"
    async with _client(handler) as client:
        [got] = await fetch_all([url], tmp_path, client=client, check_host=False)
    assert got.name == "Licitaciones con IA - Dossier.pdf"
    assert got.path.read_bytes() == PDF
    assert got.describe() == {"name": got.name, "bytes": len(PDF), "source": url}


async def test_content_disposition_wins_and_cannot_escape_the_workdir(tmp_path):
    def handler(request):
        return httpx2.Response(
            200,
            content=PDF,
            headers={"content-disposition": 'attachment; filename="../../evil.pdf"'},
        )

    async with _client(handler) as client:
        [got] = await fetch_all(
            ["https://files.example/download?id=1"],
            tmp_path,
            client=client,
            check_host=False,
        )
    assert got.name == "evil.pdf"
    assert got.path.parent == tmp_path


async def test_a_redirect_to_plain_http_is_refused(tmp_path):
    def handler(request):
        return httpx2.Response(302, headers={"location": "http://files.example/a.pdf"})

    async with _client(handler) as client:
        with pytest.raises(AttachmentError, match="https"):
            await fetch_all(
                ["https://files.example/a.pdf"],
                tmp_path,
                client=client,
                check_host=False,
            )


async def test_an_oversized_body_is_refused_even_without_content_length(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(attachments, "MAX_BYTES", 100)

    def handler(request):
        async def body():
            yield b"x" * 60
            yield b"x" * 60

        return httpx2.Response(200, content=body())

    async with _client(handler) as client:
        with pytest.raises(AttachmentError, match="larger"):
            await fetch_all(
                ["https://files.example/a.pdf"],
                tmp_path,
                client=client,
                check_host=False,
            )


async def test_an_http_error_status_is_refused(tmp_path):
    async with _client(lambda r: httpx2.Response(404)) as client:
        with pytest.raises(AttachmentError, match="404"):
            await fetch_all(
                ["https://files.example/a.pdf"],
                tmp_path,
                client=client,
                check_host=False,
            )


async def test_hosts_on_private_addresses_are_refused(tmp_path, monkeypatch):
    def resolve(host, *_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("downloaded from a private host")

    async with _client(handler) as client:
        with pytest.raises(AttachmentError, match="non-public"):
            await fetch_all(["https://intranet.example/a.pdf"], tmp_path, client=client)


async def test_two_files_with_the_same_name_are_refused(tmp_path):
    async with _client(lambda r: httpx2.Response(200, content=PDF)) as client:
        with pytest.raises(AttachmentError, match="distinct"):
            await fetch_all(
                ["https://a.example/d.pdf", "https://b.example/d.pdf"],
                tmp_path,
                client=client,
                check_host=False,
            )


def test_local_files_must_live_in_the_attachments_dir(tmp_path, monkeypatch):
    root = tmp_path / "attachments"
    root.mkdir()
    (root / "dossier.pdf").write_bytes(PDF)
    secret = tmp_path / "cookies.json"
    secret.write_text("{}")
    (root / "link.json").symlink_to(secret)
    monkeypatch.setenv(attachments.ATTACHMENTS_DIR_ENV, str(root))

    assert local("dossier.pdf").path == (root / "dossier.pdf").resolve()
    assert local(str(root / "dossier.pdf")).size == len(PDF)
    for escape in (str(secret), "../cookies.json", "link.json"):
        with pytest.raises(AttachmentError, match="inside"):
            local(escape)


def test_a_missing_local_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(attachments.ATTACHMENTS_DIR_ENV, str(tmp_path))
    with pytest.raises(AttachmentError, match="No such file"):
        local("nope.pdf")


@pytest.mark.parametrize("raw", ["", "/", "..", ".hidden"])
def test_unusable_names_are_refused(raw):
    with pytest.raises(AttachmentError):
        attachments.safe_name(raw)


def test_names_lose_path_and_control_characters():
    assert attachments.safe_name("a/b/c:d\x00.pdf") == "c_d_.pdf"
    assert attachments.safe_name(" x.pdf ") == "x.pdf"
