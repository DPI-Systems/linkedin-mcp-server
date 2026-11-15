"""Browser-DOM tests for publishing a feed post.

The share box is synthetic, built from what was measured on LinkedIn's
(``post.py``): a dialog with ``aria-labelledby`` and the ``ShareBox_textEditor``
text box, ``aria-haspopup=dialog`` buttons of which one opens a file chooser,
a media editor dialog whose last button goes on, a draft question with a
labelled close button and two unlabelled ones, and a post button that is the
composer's last. The server keeps what it accepted and serves it again. It is
a claim about the algorithm, not about LinkedIn's layout.
"""

import asyncio
import json
import pathlib

import pytest
from patchright.async_api import async_playwright

from dpi_salesnav import post
from dpi_salesnav.attachments import Attachment

pytestmark = [pytest.mark.browser_dom, pytest.mark.xdist_group("browser_runtime")]

TEXT = "Nuevo vídeo sobre licitaciones con IA #licitaciones"

SHARE = """
<h1>Feed</h1>
<div id="toast"></div>
<dialog id="composer" data-testid="dialog" aria-labelledby="dialog-header">
  <h2 id="dialog-header">Crear</h2>
  <button type="button" aria-label="Cerrar" onclick="ask()">x</button>
  <div role="button" tabindex="0" aria-expanded="false">Ana Ejemplo</div>
  <div contenteditable="true" role="textbox" componentkey="ShareBox_textEditor"></div>
  <div id="media"></div>
  <div id="emojis" hidden>:)</div>
  <button type="button" aria-label="Emoji" aria-haspopup="dialog"
          onclick="$('#emojis').hidden = false">e</button>
  <button type="button" id="mediaBtn" aria-label="Media" aria-haspopup="dialog"
          onclick="$('#file').click()">m</button>
  <button type="button" id="more" aria-label="Más" aria-haspopup="dialog">+</button>
  <button type="button" id="post" disabled onclick="publish()">Publicar</button>
</dialog>
<dialog id="editor">
  <button type="button" aria-label="Cerrar">x</button>
  <video></video>
  <button type="button">Volver</button>
  <button type="button" onclick="next()">Siguiente</button>
</dialog>
<dialog id="ask">
  <button type="button" aria-label="Cerrar">x</button>
  <button type="button" onclick="discard()">Descartar</button>
  <button type="button" onclick="flag('drafted', 'yes')">Guardar</button>
</dialog>
<input type="file" id="file" accept="video/*" hidden>
<script>
  const $ = s => document.querySelector(s);
  const flag = (k, v) => { document.body.dataset[k] = v; };
  flag('drafted', 'no');
  flag('discarded', 'no');
  let video = null;
  const box = $('[componentkey=ShareBox_textEditor]');
  const refresh = () => { $('#post').disabled = !box.innerText.trim() && !video; };
  box.addEventListener('input', refresh);
  $('#composer').show();
  $('#file').addEventListener('change', e => {
    video = e.target.files[0].name;
    $('#editor').show();
  });
  function next() {
    $('#editor').close();
    $('#media').innerHTML = '<video></video>';
    $('#mediaBtn').remove();
    $('#more').remove();
    refresh();
  }
  function ask() {
    if (!box.innerText.trim() && !video) { $('#composer').close(); return; }
    $('#ask').show();
  }
  function discard() {
    flag('discarded', 'yes');
    $('#ask').close();
    $('#composer').close();
    // As LinkedIn does: back to the feed.
    if (window.cfg.discard_navigates) location.href = '/feed/?discarded=1';
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!$('#emojis').hidden) { $('#emojis').hidden = true; return; }
    if ($('#editor').open || $('#ask').open) return;
    if ($('#composer').open) ask();
  });
  function toast() {
    $('#toast').innerHTML = '<a href="/feed/update/urn:li:activity:7/">Ver</a>';
  }
  async function publish() {
    const text = box.innerText;
    $('#composer').close();
    if (window.cfg.toast_early) toast();
    if (video) await fetch('/upload/video', {method: 'PUT', body: 'bytes'});
    await fetch('/posts', {method: 'POST', body: JSON.stringify({text, video: !!video})});
    if (!window.cfg.toast_early && !window.cfg.no_toast) toast();
  }
</script>
"""


def _post_page(stored: list[dict]) -> str:
    return "".join(
        "<article><p>{}</p>{}</article>".format(
            p["text"], "<video></video>" if p["video"] else ""
        )
        for p in stored
    )


@pytest.fixture
def clip(tmp_path: pathlib.Path) -> Attachment:
    path = tmp_path / "short.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)
    return Attachment(name="short.mp4", path=path, size=path.stat().st_size, source="t")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_pause(*_a, **_k):
        return None

    monkeypatch.setattr(post, "_pause", no_pause)
    monkeypatch.setattr(post, "_CHOOSER_TIMEOUT_MS", 1_000)
    monkeypatch.setattr(post, "_EDITOR_TIMEOUT", 5.0)
    monkeypatch.setattr(post, "_DISCARD_TIMEOUT", 3.0)
    monkeypatch.setattr(post, "_PUBLISH_TIMEOUT", 15.0)
    monkeypatch.setattr(post, "_WRITE_SETTLE", 0.5)
    monkeypatch.setattr(post, "_VERIFY_TIMEOUT", 1.0)


@pytest.fixture
async def linkedin():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chromium", headless=True)
            page = await (await browser.new_context()).new_page()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"chromium unavailable: {exc}")
        state: dict = {"stored": [], "requests": []}

        async def serve(cfg: dict) -> dict:
            await page.context.add_init_script(f"window.cfg = {json.dumps(cfg)};")

            async def handler(route):
                request = route.request
                path = request.url.split("linkedin.com", 1)[1]
                state["requests"].append(f"{request.method} {path.split('?')[0]}")
                if request.method == "PUT":
                    await asyncio.sleep(cfg.get("upload_delay", 0))
                    await route.fulfill(status=201, body="")
                    return
                if request.method == "POST":
                    if not cfg.get("server_drops"):
                        state["stored"].append(json.loads(request.post_data or "{}"))
                    await route.fulfill(
                        status=500 if cfg.get("server_drops") else 201, body="{}"
                    )
                    return
                if path.startswith("/in/me/"):
                    await route.fulfill(
                        status=302, headers={"location": "/in/ana-ejemplo/"}
                    )
                    return
                if "/feed/update/" in path or "/recent-activity/" in path:
                    body = _post_page(state["stored"])
                elif path.startswith("/feed/?discarded"):
                    await asyncio.sleep(cfg.get("feed_delay", 0))
                    body = "<h1>Feed</h1>"
                elif path.startswith("/feed/"):
                    body = SHARE
                else:
                    body = "<h1>Perfil</h1>"
                await route.fulfill(content_type="text/html; charset=utf-8", body=body)

            await page.context.route("https://www.linkedin.com/**", handler)
            return state

        try:
            yield page, serve
        finally:
            await browser.close()


async def _flags(page) -> dict:
    return await page.evaluate(
        "() => ({drafted: document.body.dataset.drafted === 'yes',"
        " discarded: document.body.dataset.discarded === 'yes',"
        " open: !!document.querySelector('dialog[open]')})"
    )


async def test_a_dry_run_takes_text_and_video_then_discards_them(linkedin, clip):
    page, serve = linkedin
    state = await serve({})
    result = await post.create_post(page, TEXT, video=clip)
    assert result["status"] == "preview"
    assert result["post_button_enabled"] is True
    assert result["video"] == {"name": "short.mp4", "bytes": clip.size, "source": "t"}
    assert await _flags(page) == {"drafted": False, "discarded": True, "open": False}
    assert not [r for r in state["requests"] if not r.startswith("GET")]


async def test_a_slow_return_to_the_feed_after_discarding_is_no_failure(linkedin, clip):
    page, serve = linkedin
    page.set_default_timeout(1_000)
    state = await serve({"discard_navigates": True, "feed_delay": 2.5})
    result = await post.create_post(page, TEXT, video=clip)
    assert result["status"] == "preview"
    assert "GET /feed/" in state["requests"][-1]


async def test_a_dry_run_of_text_only_discards_it(linkedin):
    page, serve = linkedin
    await serve({})
    result = await post.create_post(page, TEXT)
    assert result["status"] == "preview"
    assert result["video"] is None
    assert (await _flags(page))["discarded"] is True


async def test_a_published_video_post_is_found_where_linkedin_serves_it(linkedin, clip):
    page, serve = linkedin
    state = await serve({})
    result = await post.create_post(page, TEXT, video=clip, confirm_publish=True)
    assert result["status"] == "published"
    assert "/feed/update/urn:li:activity:7/" in result["url"]
    assert result["verified_after_reload"]["videos"] == 1
    assert state["stored"] == [{"text": TEXT, "video": True}]


async def test_the_page_is_not_left_before_the_upload_is_done(linkedin, clip):
    page, serve = linkedin
    # The "view post" notice shows at once; the video is still going up.
    state = await serve({"toast_early": True, "upload_delay": 2.0})
    result = await post.create_post(page, TEXT, video=clip, confirm_publish=True)
    assert result["upload_seconds"] >= 2.0
    assert state["stored"] == [{"text": TEXT, "video": True}]
    assert result["status"] == "published"


async def test_a_post_the_server_refused_is_unconfirmed(linkedin, clip):
    page, serve = linkedin
    await serve({"server_drops": True})
    result = await post.create_post(page, TEXT, video=clip, confirm_publish=True)
    assert result["status"] == "unconfirmed"
    assert result["verified_after_reload"]["verified"] is False


async def test_without_a_link_the_post_is_looked_for_in_recent_activity(linkedin):
    page, serve = linkedin
    await serve({"no_toast": True})
    result = await post.create_post(page, TEXT, confirm_publish=True)
    assert result["status"] == "published"
    assert result["url"].endswith("/in/ana-ejemplo/recent-activity/all/")


async def test_a_file_that_is_not_a_video_is_refused(tmp_path):
    path = tmp_path / "dossier.pdf"
    path.write_bytes(b"%PDF")
    pdf = Attachment(name="dossier.pdf", path=path, size=4, source="t")
    with pytest.raises(ValueError, match="not a video"):
        await post.create_post(object(), TEXT, video=pdf)
