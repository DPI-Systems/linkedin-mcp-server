"""The least gap between Sales Navigator searches holds across calls."""

import asyncio

import pytest

from dpi_salesnav import people


@pytest.fixture
async def clock(monkeypatch):
    """Virtual time: sleeping moves the loop clock forward instead of waiting."""
    now = {"t": 1000.0}
    slept = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: now["t"])

    async def sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr(people.asyncio, "sleep", sleep)
    monkeypatch.setattr(people.random, "uniform", lambda a, b: b)
    monkeypatch.setattr(people, "_last_search", None)
    monkeypatch.delenv(people.MIN_SEARCH_GAP_ENV, raising=False)
    return now, slept


async def test_the_first_search_does_not_wait(clock):
    assert await people.wait_for_search_turn() == 0


async def test_a_search_right_after_another_waits_the_gap_plus_jitter(clock):
    now, slept = clock
    await people.wait_for_search_turn()
    now["t"] += 5
    await people.wait_for_search_turn()
    assert slept == [pytest.approx(20 + 10 - 5)]


async def test_a_search_after_the_gap_goes_at_once(clock):
    now, slept = clock
    await people.wait_for_search_turn()
    now["t"] += 31
    await people.wait_for_search_turn()
    assert slept == []


async def test_the_gap_can_be_set_and_switched_off(clock, monkeypatch):
    now, slept = clock
    monkeypatch.setenv(people.MIN_SEARCH_GAP_ENV, "0")
    await people.wait_for_search_turn()
    await people.wait_for_search_turn()
    assert slept == []
