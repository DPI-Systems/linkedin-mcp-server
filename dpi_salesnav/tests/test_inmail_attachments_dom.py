"""Browser-DOM tests for attaching files to an InMail.

The composer is synthetic: the attributes ``inmail.py`` selects on (the
message button, the dialog, the compose form, the send button, a file input)
around a pill that shows the file name with one button to remove it, a
progress bar while "uploading", and a thread that lists a sent message. It is
a claim about the algorithm (a file is found and removed by its name, never by
translated text), not about Sales Navigator's layout.
"""

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
    <button type="button" data-sales-action onclick="send()">Enviar</button>
  </form>
</section>
<script>
  const $ = s => document.querySelector(s);
  function openComposer() { $('#dialog').hidden = false; }
  function closeComposer() { $('#dialog').hidden = true; }
  const sendBtn = () => $('[data-sales-action]');
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
      pill.innerHTML = '<span class="n"></span><span role="progressbar"></span>' +
        '<button type="button" aria-label="Quitar">x</button>';
      pill.querySelector('.n').textContent = f.name;
      pill.querySelector('button').onclick = () => pill.remove();
      $('#files').appendChild(pill);
      if (!window.cfg.enabled_while_uploading) sendBtn().disabled = true;
      if (!window.cfg.stuck) setTimeout(() => {
        pill.querySelector('[role=progressbar]').remove();
        sendBtn().disabled = false;
      }, 300);
    }
  });
  function send() {
    const li = document.createElement('li');
    li.textContent = $('textarea').value;
    if (window.cfg.thread_shows_file) {
      for (const p of document.querySelectorAll('#files .pill')) {
        const a = document.createElement('a');
        a.textContent = p.querySelector('.n').textContent;
        li.appendChild(a);
      }
    }
    $('#thread').appendChild(li);
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


@pytest.fixture
async def composer():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chromium", headless=True)
            page = await browser.new_page()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"chromium unavailable: {exc}")

        async def serve(cfg: dict, html: str = PAGE) -> None:
            await page.add_init_script(f"window.cfg = {__import__('json').dumps(cfg)};")

            async def handler(route):
                await route.fulfill(content_type="text/html", body=html)

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
