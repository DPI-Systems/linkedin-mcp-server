"""Browser-DOM test for the catch-up card extractor.

The markup is synthetic: a list of cards, each with a profile link (image and
name), LinkedIn's message button and, for job changes, a link to the
announcement with its counters; plus a sidebar link to a profile with no card
around it. It is a claim about which signals are read (profile links, a button
with an aria-label, activity links), not about LinkedIn's layout.
"""

import pytest
from patchright.async_api import async_playwright

from dpi_salesnav.celebrations import _EXTRACT

pytestmark = [pytest.mark.browser_dom, pytest.mark.xdist_group("browser_runtime")]

HTML = """
<main>
  <aside><a href="/in/sidebar-person/">Someone in the sidebar</a></aside>
  <ul>
    <li><div>
      <a href="https://www.linkedin.com/in/ana/"><img alt=""></a>
      <a href="https://www.linkedin.com/feed/?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A7&x=1">
        <p>Ana Ejemplo</p><p>New position: CTO at Acme</p></a>
      <p>26 reactions</p><p>26</p>
      <button aria-label="Message Ana: Congrats!">Congrats on the…</button>
    </div></li>
    <li><div>
      <a href="https://www.linkedin.com/in/bob/"><img alt=""></a>
      <p>Bob Ejemplo</p><p>Birthday today</p>
      <button aria-label="Message Bob: Happy birthday!">Happy birthday!</button>
    </div></li>
  </ul>
</main>
"""


@pytest.fixture
async def dom_page():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chromium", headless=True)
            page = await browser.new_page()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            yield page
        finally:
            await browser.close()


async def test_one_card_per_profile_with_text_but_not_buttons_or_counters(dom_page):
    await dom_page.set_content(HTML)
    cards = await dom_page.evaluate(_EXTRACT)
    assert [c["profile_path"] for c in cards] == ["/in/ana", "/in/bob"]
    ana, bob = cards
    assert ana["lines"] == ["Ana Ejemplo", "New position: CTO at Acme", "26 reactions"]
    assert "activity%3A7" in ana["activity_href"]
    assert bob["lines"] == ["Bob Ejemplo", "Birthday today"]
    assert bob["activity_href"] is None
