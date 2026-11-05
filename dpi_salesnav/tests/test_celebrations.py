"""Offline tests for the catch-up page reader (synthetic data only)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from dpi_salesnav import celebrations
from dpi_salesnav.celebrations import (
    entry_from_card,
    read_celebrations,
    validate_categories,
)


def test_card_becomes_an_entry_with_username_and_post():
    card = {
        "profile_path": "/in/ana-ejemplo-123",
        "lines": [
            "Ana Ejemplo",
            "Started a new position as CTO at Acme",
            "26 reactions",
        ],
        "activity_href": "https://www.linkedin.com/feed/?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A42&x=1",
    }
    entry = entry_from_card(card, "job_changes")
    assert entry == {
        "category": "job_changes",
        "name": "Ana Ejemplo",
        "detail": "Started a new position as CTO at Acme",
        "linkedin_username": "ana-ejemplo-123",
        "profile_url": "https://www.linkedin.com/in/ana-ejemplo-123/",
        "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:42/",
    }


def test_username_is_decoded_but_profile_url_keeps_linkedins_encoding():
    entry = entry_from_card(
        {"profile_path": "/in/jos%C3%A9-p/", "lines": ["José P"]}, "birthdays"
    )
    assert entry is not None
    assert entry["linkedin_username"] == "josé-p"
    assert entry["profile_url"] == "https://www.linkedin.com/in/jos%C3%A9-p/"
    assert entry["post_url"] is None and entry["detail"] is None


def test_card_without_a_profile_path_is_dropped():
    assert (
        entry_from_card({"profile_path": "/company/acme/", "lines": ["x"]}, "x") is None
    )


def test_categories_default_to_all_and_reject_unknown():
    assert validate_categories(None) == list(celebrations.CATEGORIES)
    assert validate_categories(["birthdays", "birthdays"]) == ["birthdays"]
    with pytest.raises(ValueError, match="cumpleanos"):
        validate_categories(["cumpleanos"])


def _page(urls_after_goto, cards_by_slug):
    page = MagicMock()
    page.url = ""
    visited = []

    async def goto(url, **_):
        visited.append(url)
        page.url = urls_after_goto.get(url, url)

    async def evaluate(_js):
        slug = visited[-1].rstrip("/").rsplit("/", 1)[-1]
        result = cards_by_slug[slug]
        if isinstance(result, Exception):
            raise result
        return result

    page.goto = goto
    page.evaluate = evaluate
    page.mouse.wheel = AsyncMock()
    return page, visited


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(celebrations.asyncio, "sleep", AsyncMock())


CARD = {"profile_path": "/in/a", "lines": ["A", "detail"]}


async def test_one_navigation_per_category_and_entries_keep_their_category():
    page, visited = _page({}, {"birthday": [CARD], "job_changes": []})
    out = await read_celebrations(page, ["birthdays", "job_changes"])
    assert visited == [
        "https://www.linkedin.com/mynetwork/catch-up/birthday/",
        "https://www.linkedin.com/mynetwork/catch-up/job_changes/",
    ]
    assert out == {
        "categories": {
            "birthdays": [entry_from_card(CARD, "birthdays")],
            "job_changes": [],
        }
    }


async def test_a_failing_or_redirected_category_is_an_error_not_a_loss():
    page, _ = _page(
        {
            "https://www.linkedin.com/mynetwork/catch-up/education/": "https://www.linkedin.com/checkpoint/x"
        },
        {"birthday": RuntimeError("boom"), "job_changes": [CARD], "education": [CARD]},
    )
    out = await read_celebrations(page, ["birthdays", "job_changes", "education"])
    assert out["categories"] == {"job_changes": [entry_from_card(CARD, "job_changes")]}
    assert set(out["errors"]) == {"birthdays", "education"}


async def test_scrolling_stops_when_no_new_cards_and_caps_results():
    page, _ = _page({}, {"birthday": [CARD] * 3})
    out = await read_celebrations(page, ["birthdays"], max_results=2)
    assert len(out["categories"]["birthdays"]) == 2
