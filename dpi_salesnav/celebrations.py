"""Network updates from LinkedIn's "Catch up" page: birthdays, job changes,
work anniversaries and education milestones of the viewer's connections.

Each category is its own URL, so the category of an entry comes from the
navigation and never from translated text. An entry is located by its profile
link; its text lines are returned as LinkedIn rendered them (the date of a
birthday, the new position), in the account's language.
"""

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import unquote, urlparse


logger = logging.getLogger(__name__)

CATCH_UP_URL = "https://www.linkedin.com/mynetwork/catch-up/{}/"
CATEGORIES = {
    "birthdays": "birthday",
    "job_changes": "job_changes",
    "work_anniversaries": "work_anniversaries",
    "education": "education",
}
_SETTLE = 6.0
_SCROLL_PAUSE = (1.5, 3.0)
_MAX_SCROLLS = 8
_VANITY = re.compile(r"^/in/([^/]+)/?")
_ACTIVITY = re.compile(r"urn(?::|%3A)li(?::|%3A)activity(?::|%3A)(\d+)", re.IGNORECASE)

# One card per profile link: climb from the link while the ancestor still
# names only that one profile. No class names, no translated text.
_EXTRACT = r"""() => {
  const main = document.querySelector('main') || document.body;
  const vanity = a => {
    try { const m = new URL(a.href).pathname.match(/^\/in\/[^/]+/); return m ? m[0] : null; }
    catch (e) { return null; }
  };
  const profilesIn = el => new Set([...el.querySelectorAll('a[href*="/in/"]')].map(vanity).filter(Boolean));
  const seen = new Set();
  const out = [];
  for (const a of main.querySelectorAll('a[href*="/in/"]')) {
    const v = vanity(a);
    if (!v || seen.has(v)) continue;
    let card = a;
    while (card.parentElement && card.parentElement !== main && profilesIn(card.parentElement).size === 1) {
      card = card.parentElement;
    }
    if (card === a || !card.querySelector('button[aria-label]')) continue;
    seen.add(v);
    const buttons = new Set([...card.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean));
    const lines = card.innerText.split('\n').map(s => s.trim())
      .filter(s => s && !buttons.has(s) && !/^\d+$/.test(s));
    const activity = [...card.querySelectorAll('a[href*="activity"]')].map(x => x.href)[0] || null;
    out.push({profile_path: v, lines, activity_href: activity});
  }
  return out;
}"""


def entry_from_card(card: dict[str, Any], category: str) -> dict[str, Any] | None:
    m = _VANITY.match(card.get("profile_path") or "")
    if not m:
        return None
    username = unquote(m.group(1))
    lines = [s for s in card.get("lines") or [] if isinstance(s, str)]
    # LinkedIn's counters ("26 reacciones") are noise for the caller.
    lines = [s for s in lines if not re.match(r"^\d[\d.,]*\s+\S+$", s)]
    activity = _ACTIVITY.search(card.get("activity_href") or "")
    return {
        "category": category,
        "name": lines[0] if lines else None,
        "detail": lines[1] if len(lines) > 1 else None,
        "linkedin_username": username,
        "profile_url": f"https://www.linkedin.com/in/{m.group(1)}/",
        "post_url": (
            f"https://www.linkedin.com/feed/update/urn:li:activity:{activity.group(1)}/"
            if activity
            else None
        ),
    }


def validate_categories(categories: list[str] | None) -> list[str]:
    if not categories:
        return list(CATEGORIES)
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise ValueError(
            f"Unknown categories {unknown}; choose from {list(CATEGORIES)}"
        )
    return list(dict.fromkeys(categories))


async def _read_category(
    page: Any, slug: str, max_results: int
) -> list[dict[str, Any]]:
    await page.goto(
        CATCH_UP_URL.format(slug), wait_until="domcontentloaded", timeout=30_000
    )
    await asyncio.sleep(_SETTLE)
    cards = await page.evaluate(_EXTRACT)
    for _ in range(_MAX_SCROLLS):
        if len(cards) >= max_results:
            break
        await page.mouse.wheel(0, 4000)
        await asyncio.sleep(random.uniform(*_SCROLL_PAUSE))
        more = await page.evaluate(_EXTRACT)
        if len(more) <= len(cards):
            break
        cards = more
    return cards


async def read_celebrations(
    page: Any, categories: list[str] | None = None, *, max_results: int = 30
) -> dict[str, Any]:
    """Read the chosen catch-up categories, one navigation each."""
    out: dict[str, Any] = {"categories": {}, "errors": {}}
    for i, category in enumerate(validate_categories(categories)):
        if i:
            await asyncio.sleep(random.uniform(3.0, 7.0))
        slug = CATEGORIES[category]
        try:
            cards = await _read_category(page, slug, max_results)
        except Exception as exc:  # one category failing keeps the others
            logger.warning("catch-up %s failed: %s", slug, exc)
            out["errors"][category] = str(exc)
            continue
        if "/mynetwork/catch-up/" not in urlparse(page.url).path:
            out["errors"][category] = f"LinkedIn sent the page to {page.url}"
            continue
        entries = [e for c in cards if (e := entry_from_card(c, category))]
        out["categories"][category] = entries[:max_results]
    if not out["errors"]:
        del out["errors"]
    return out
