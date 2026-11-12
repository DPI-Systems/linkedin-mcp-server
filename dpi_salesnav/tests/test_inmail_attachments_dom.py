"""Browser-DOM tests for attaching files to an InMail.

The composer is synthetic: the attributes ``inmail.py`` selects on (the
message button, the dialog, the compose form, the send button, a file input)
around a pill that shows the file name with one button to remove it, a
progress bar that stays in place once full (as Sales Navigator's does), and a
thread that lists a sent message. It is
a claim about the algorithm (a file is found and removed by its name, never by
translated text), not about Sales Navigator's layout.
"""

import asyncio
import json
import pathlib

import pytest
from patchright.async_api import async_playwright

from dpi_salesnav import inmail
from dpi_salesnav.attachments import Attachment

pytestmark = [pytest.mark.browser_dom, pytest.mark.xdist_group("browser_runtime")]

LEAD = "https://www.linkedin.com/sales/lead/ACwAAA,NAME_SEARCH,abcd"
NAME = "Licitaciones con IA - Dossier.pdf"

PAGE = """
<button data-anchor-send-inmail onclick="openComposer()">Mensaje</button>
<section id="dialog" role="dialog" data-message-overlay-container
         aria-label="Ana Ejemplo" hidden>
  <button data-control-name="overlay.close_overlay" onclick="closeComposer()">x</button>
  <ul id="thread"></ul>
  <form data-x-conversation-widget="compose-form" onsubmit="return false">
    <input id="compose-form-subject-1">
    <textarea name="message"></textarea>
    <div id="files"></div>
    <label><input type="file" id="file" hidden></label>
    <button type="button" aria-label="Emoji">:)</button>
    <button type="button" data-sales-action>copy</button>
    <span><button type="button" data-sales-action data-x--miq aria-label="Create draft">AI</button></span>
    <button type="button" data-sales-action aria-disabled="false" onclick="send()">Enviar</button>
  </form>
</section>
<script>
  const $ = s => document.querySelector(s);
  function openComposer() { $('#dialog').hidden = false; }
  function closeComposer() { $('#dialog').hidden = true; }
  const sendBtn = () => $('[data-sales-action][aria-disabled]');
  $('#file').addEventListener('change', e => {
    for (const f of e.target.files) {
      const got = document.createElement('i');
      got.className = 'received';
      got.dataset.name = f.name;
      got.dataset.size = f.size;
      got.hidden = true;
      document.body.appendChild(got);
      const pill = document.createElement('div');
      pill.className = 'pill';
      pill.innerHTML = '<span class="n"></span><progress max="100" value="30"></progress>' +
        '<button type="button" aria-label="Quitar">x</button>';
      pill.querySelector('.n').textContent = f.name;
      pill.querySelector('button').onclick = () => pill.remove();
      $('#files').appendChild(pill);
      if (!window.cfg.enabled_while_uploading) { sendBtn().disabled = true; sendBtn().setAttribute('aria-disabled', 'true'); }
      if (!window.cfg.stuck) setTimeout(() => {
        pill.querySelector('progress').value = 100;
        sendBtn().disabled = false;
        sendBtn().setAttribute('aria-disabled', 'false');
      }, 300);
    }
  });
  function send() {
    const li = document.createElement('li');
    li.textContent = $('textarea').value;
    const files = [];
    if (window.cfg.thread_shows_file) {
      for (const p of document.querySelectorAll('#files .pill')) {
        const a = document.createElement('a');
        a.textContent = p.querySelector('.n').textContent;
        files.push(a.textContent);
        li.appendChild(a);
      }
    }
    // Drawn at once, as Sales Navigator does; the server may still refuse it.
    $('#thread').appendChild(li);
    fetch('https://www.linkedin.com/sales-api/messages', {
      method: 'POST', body: JSON.stringify({
        subject: $('#compose-form-subject-1').value, body: li.firstChild.textContent, files,
      }),
    });
    $('textarea').value = '';
    $('#files').innerHTML = '';
  }
</script>
"""


@pytest.fixture
def dossier(tmp_path: pathlib.Path) -> Attachment:
    path = tmp_path / NAME
    path.write_bytes(b"%PDF-1.4\n" + b"0" * 2048)
    return Attachment(name=NAME, path=path, size=path.stat().st_size, source="t")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_pause(*_a, **_k):
        return None

    monkeypatch.setattr(inmail, "_human_pause", no_pause)
    monkeypatch.setattr(inmail, "_UPLOAD_TIMEOUT", 3.0)
    monkeypatch.setattr(inmail, "_REMOVE_TIMEOUT", 2.0)
    monkeypatch.setattr(inmail, "_SEND_TIMEOUT", 2.0)
    monkeypatch.setattr(inmail, "_THREAD_ATTACHMENT_TIMEOUT", 0.0)
    monkeypatch.setattr(inmail, "_VERIFY_TIMEOUT", 1.0)
    monkeypatch.setattr(inmail, "_OPENER_TIMEOUT_MS", 1_500)


@pytest.fixture
async def composer():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chromium", headless=True)
            page = await browser.new_page()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"chromium unavailable: {exc}")

        async def serve(cfg: dict, html: str = PAGE, thread: list | None = None):
            await page.add_init_script(f"window.cfg = {json.dumps(cfg)};")
            # What the server has accepted, served again with every load.
            stored = thread if thread is not None else []

            async def handler(route):
                request = route.request
                if request.method == "POST":
                    if cfg.get("server_drops"):
                        await route.fulfill(status=500, body="{}")
                        return
                    stored.append(json.loads(request.post_data or "{}"))
                    await route.fulfill(content_type="application/json", body="{}")
                    return
                items = "".join(
                    "<li>{}{}{}</li>".format(
                        ""
                        if cfg.get("thread_hides_subject")
                        else f"<b>{m.get('subject', '')}</b> ",
                        m["body"],
                        "".join(f"<a>{f}</a>" for f in m["files"]),
                    )
                    for m in stored
                )
                await route.fulfill(
                    content_type="text/html",
                    body=html.replace(
                        '<ul id="thread"></ul>', f'<ul id="thread">{items}</ul>'
                    ),
                )

            await page.route("https://www.linkedin.com/**", handler)

        try:
            yield page, serve
        finally:
            await browser.close()


async def _state(page):
    return await page.evaluate(
        """() => ({
            received: [...document.querySelectorAll('i.received')].map(
                e => ({name: e.dataset.name, size: Number(e.dataset.size)})),
            pills: document.querySelectorAll('#files .pill').length,
            body: document.querySelector('textarea').value,
            open: !document.querySelector('#dialog').hidden,
            sent: document.querySelectorAll('#thread li').length,
        })"""
    )


async def test_dry_run_attaches_verifies_then_removes_and_closes(composer, dossier):
    page, serve = composer
    await serve({})
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    assert result["status"] == "preview"
    assert result["attachments_verified"] is True
    assert result["attachments_removed"] == [NAME]
    assert result["lead_load_attempts"] == 1
    assert 0 <= result["lead_load_seconds"] < 1.5
    assert result["attachments"] == [
        {"name": NAME, "bytes": dossier.size, "source": "t"}
    ]
    state = await _state(page)
    assert state["received"] == [{"name": NAME, "size": dossier.size}]
    assert state == {**state, "pills": 0, "body": "", "open": False, "sent": 0}


async def test_an_upload_that_never_finishes_is_refused_and_cleaned(composer, dossier):
    page, serve = composer
    await serve({"stuck": True, "enabled_while_uploading": True})
    with pytest.raises(inmail.SendRefused, match="could not be verified"):
        await inmail.send_inmail(
            page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
        )
    state = await _state(page)
    assert state["sent"] == 0
    assert (state["pills"], state["body"], state["open"]) == (0, "", False)


async def test_a_composer_without_a_file_input_sends_nothing(composer, dossier):
    page, serve = composer
    await serve({}, PAGE.replace('<input type="file" id="file" hidden>', ""))
    with pytest.raises(inmail.SendRefused, match="file input"):
        await inmail.send_inmail(
            page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
        )
    state = await _state(page)
    assert (state["sent"], state["body"], state["open"]) == (0, "", False)


async def test_sent_needs_the_file_name_in_the_thread(composer, dossier):
    page, serve = composer
    await serve({"thread_shows_file": True})
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
    )
    assert result["status"] == "sent"
    assert result["attachments_in_thread"] == [NAME]
    assert result["verified_after_reload"]["verified"] is True
    assert {"status": 200, "url": "www.linkedin.com/sales-api/messages"} in result[
        "post_responses"
    ]


async def test_a_message_drawn_but_refused_by_the_server_is_unconfirmed(
    composer, dossier
):
    page, serve = composer
    await serve({"thread_shows_file": True, "server_drops": True})
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
    )
    # The composer showed text and file, as on account 2.
    assert result["text_in_thread"] is True
    assert result["attachments_in_thread"] == [NAME]
    assert result["status"] == "unconfirmed"
    assert result["verified_after_reload"]["verified"] is False
    assert {"status": 500, "url": "www.linkedin.com/sales-api/messages"} in result[
        "post_responses"
    ]


async def test_a_thread_without_the_inmails_subject_is_unconfirmed(composer):
    page, serve = composer
    await serve({"thread_hides_subject": True})
    result = await inmail.send_inmail(page, LEAD, "Asunto", "Hola", confirm_send=True)
    assert result["verified_after_reload"]["text"] is True
    assert result["verified_after_reload"]["subject"] is False
    assert result["status"] == "unconfirmed"


async def test_an_earlier_copy_of_the_text_does_not_confirm_a_new_send(composer):
    page, serve = composer
    await serve({"server_drops": True}, thread=[{"body": "Hola", "files": []}])
    result = await inmail.send_inmail(page, LEAD, "Asunto", "Hola", confirm_send=True)
    assert result["status"] == "unconfirmed"
    assert result["verified_after_reload"]["copies_before"] == 1


async def test_a_thread_without_the_file_is_unconfirmed(composer, dossier):
    page, serve = composer
    await serve({"thread_shows_file": False})
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
    )
    assert result["status"] == "unconfirmed"
    assert result["text_in_thread"] is True
    assert result["attachments_in_thread"] == []
    assert NAME in result["message"]


SPLASH = "<title>Sales Navigator</title><body></body>"
# Past the splash: titled after the person, but its button comes only after
# the first wait has run out (1.5 s in these tests).
LATE_BUTTON = PAGE.replace("data-anchor-send-inmail", "data-late").replace(
    "<script>",
    "<title>Ana Ejemplo | Sales Navigator</title><script>"
    "setTimeout(() => document.querySelector('[data-late]')"
    ".setAttribute('data-anchor-send-inmail', ''), 2200);",
)


async def _serve_loads(page, *pages):
    loads = []

    async def handler(route):
        loads.append(route.request.url)
        html = pages[min(len(loads), len(pages)) - 1]
        await route.fulfill(content_type="text/html", body=html)

    await page.add_init_script("window.cfg = {};")
    await page.route("https://www.linkedin.com/**", handler)
    return loads


async def test_a_lead_page_shown_at_once_takes_the_first_path(composer, dossier):
    page, serve = composer
    await serve({})
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    assert result["lead_load_path"] == "first"


async def test_a_lead_page_still_on_its_splash_is_loaded_once_more(composer, dossier):
    page, _ = composer
    loads = await _serve_loads(page, SPLASH, PAGE)
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    assert result["status"] == "preview"
    assert len(loads) == 2
    assert result["lead_load_attempts"] == 2
    assert result["lead_load_path"] == "reloaded"


async def test_a_lead_page_past_its_splash_is_waited_on_not_reloaded(composer, dossier):
    page, _ = composer
    # A reload would serve a page without any button, and fail.
    loads = await _serve_loads(page, LATE_BUTTON, SPLASH)
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    assert result["status"] == "preview"
    assert len(loads) == 1
    assert result["lead_load_attempts"] == 1
    assert result["lead_load_path"] == "waited_more"
    assert result["lead_load_seconds"] >= 2.2


async def test_a_lead_page_that_never_shows_its_button_is_refused(composer, dossier):
    page, serve = composer
    await serve({}, PAGE.replace("data-anchor-send-inmail", ""))
    with pytest.raises(inmail.SendRefused, match="twice") as caught:
        await inmail.send_inmail(
            page, LEAD, "Asunto", "Hola", confirm_send=True, attachments=[dossier]
        )
    assert (await _state(page))["sent"] == 0
    assert "waited_more" in str(caught.value)


async def test_a_stuck_splash_says_what_it_waits_for(composer, dossier):
    page, _ = composer
    release = asyncio.Event()
    splash = SPLASH.replace(
        "<body></body>",
        "<body><script>fetch('https://www.linkedin.com/sales-api/hang')</script></body>",
    )

    async def handler(route):
        if route.request.url.endswith("/hang"):
            await release.wait()
            await route.abort()
            return
        await route.fulfill(content_type="text/html", body=splash)

    await page.add_init_script("window.cfg = {};")
    await page.route("https://www.linkedin.com/**", handler)
    try:
        with pytest.raises(inmail.SendRefused, match="reloaded") as caught:
            await inmail.send_inmail(page, LEAD, "Asunto", "Hola", confirm_send=False)
    finally:
        release.set()
    text = str(caught.value)
    assert "www.linkedin.com/sales-api/hang" in text
    assert "'visibility': 'visible'" in text
    assert "'after_screenshot': {" in text
    assert "'has_message_button': False" in text


async def _through_wrapper(page, **kwargs):
    from dpi_salesnav import failures

    @failures.with_failure_artifacts
    async def salesnav_send_inmail():
        failures.use_page(page)
        return await inmail.send_inmail(page, LEAD, "Asunto", "Hola", **kwargs)

    return await salesnav_send_inmail()


async def test_a_composer_that_never_opens_keeps_the_page(
    composer, dossier, tmp_path, monkeypatch
):
    from dpi_salesnav import failures
    from linkedin_mcp_server.error_handler import CodedToolError

    monkeypatch.setenv(failures.FAILURES_DIR_ENV, str(tmp_path / "f"))
    monkeypatch.setattr(inmail, "_COMPOSER_TIMEOUT_MS", 500)
    page, serve = composer
    await serve({}, PAGE.replace('onclick="openComposer()"', ""))
    with pytest.raises(CodedToolError, match='step "open_composer"') as caught:
        await _through_wrapper(page, confirm_send=False, attachments=[dossier])
    assert "TimeoutError" in str(caught.value)
    [run] = list((tmp_path / "f").iterdir())
    assert (run / "viewport.jpg").stat().st_size > 0
    assert "data-anchor-send-inmail" in (run / "page.html").read_text()


async def test_a_refused_attachment_is_captured_before_the_composer_is_emptied(
    composer, dossier, tmp_path, monkeypatch
):
    from dpi_salesnav import failures
    from linkedin_mcp_server.error_handler import CodedToolError

    monkeypatch.setenv(failures.FAILURES_DIR_ENV, str(tmp_path / "f"))
    page, serve = composer
    await serve({"stuck": True, "enabled_while_uploading": True})
    with pytest.raises(CodedToolError, match='step "attach"'):
        await _through_wrapper(page, confirm_send=True, attachments=[dossier])
    [run] = list((tmp_path / "f").iterdir())
    assert 'class="pill"' in (run / "page.html").read_text()
    assert (await _state(page))["pills"] == 0


async def test_the_result_names_the_lead_pages_long_tasks(composer, dossier):
    page, serve = composer
    busy = (
        "setTimeout(() => { const t = performance.now();"
        " while (performance.now() - t < 300) {} }, 20)"
    )
    await serve({}, PAGE.replace("<script>", f"<script>{busy};", 1))
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    tasks = result["lead_load_longtasks"]
    assert tasks["count"] >= 1
    assert tasks["longest"][0]["duration_ms"] >= 290
    assert tasks["frames"][0]["duration_ms"] >= 290
    assert tasks["visibility"][0]["state"] == "visible"
    assert "renderer_cpu" not in result
    assert "lead_load_renderer_cpu" in result


async def test_a_lead_page_that_stops_answering_is_loaded_in_a_new_page(
    composer, dossier, monkeypatch
):
    first, _ = composer
    browser = first.context.browser
    assert browser is not None
    # A context of its own: the fixture's page cannot open a second page.
    page = await (await browser.new_context()).new_page()
    monkeypatch.setattr(inmail, "_STATE_TIMEOUT", 0.5)
    page.set_default_timeout(1000)
    # Loaded, then busy for longer than the wait and every read of its state.
    hung = SPLASH.replace(
        "<body></body>",
        "<body><script>setTimeout(() => { const t = Date.now();"
        " while (Date.now() - t < 8000) {} }, 200)</script></body>",
    )
    loads = []

    async def handler(route):
        loads.append(route.request.url)
        await route.fulfill(
            content_type="text/html", body=hung if len(loads) == 1 else PAGE
        )

    await page.context.add_init_script("window.cfg = {};")
    await page.context.route("https://www.linkedin.com/**", handler)
    result = await inmail.send_inmail(
        page, LEAD, "Asunto", "Hola", confirm_send=False, attachments=[dossier]
    )
    assert result["status"] == "preview"
    assert result["lead_load_path"] == "new_page"
    assert len(loads) == 2
    assert page.is_closed()
