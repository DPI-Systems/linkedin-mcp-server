"""Offline tests for the verification viewer (no browser, no VNC processes)."""

import asyncio
from types import SimpleNamespace

import pytest

from dpi_salesnav import verification as v

LOCAL = "http://127.0.0.1:6080/login.html#token=abc"


def test_public_url_moves_the_viewer_onto_the_public_origin():
    out = v.public_url(LOCAL, "https://linkedin-verify.example/")
    assert out == "https://linkedin-verify.example/login.html#token=abc"


def test_public_url_without_a_base_keeps_the_loopback_address():
    assert v.public_url(LOCAL, "") == LOCAL


@pytest.mark.parametrize(
    ("url", "state"),
    [
        ("https://www.linkedin.com/checkpoint/challenge/AgF123", "challenge"),
        ("https://www.linkedin.com/uas/login?session_redirect=x", "challenge"),
        ("https://www.linkedin.com/checkpoint/login/login-restriction", "restricted"),
        ("https://www.linkedin.com/feed/", "ok"),
        ("https://www.linkedin.com/in/checkpoint-fan/", "ok"),
        ("about:blank", "off_linkedin"),
    ],
)
def test_page_state_reads_the_route_only(url, state):
    assert v.page_state(url) == state


def test_a_captcha_frame_inside_a_search_is_a_challenge():
    search = "https://www.linkedin.com/sales/search/people?query=x"
    frame = "https://www.linkedin.com/help/api/hip/challenge?x=1"
    assert v.page_state(search, (search, frame)) == "challenge"
    assert v.page_state(search, (search,)) == "ok"


class FakeViewer:
    instances: list["FakeViewer"] = []

    def __init__(self):
        self.started = False
        self.stopped = False
        FakeViewer.instances.append(self)

    def start_window_manager(self):
        self.started = True

    def start_remote_control(self):
        return LOCAL

    def stop_window_manager(self):
        self.stopped = True


class FakePage:
    def __init__(self, url):
        self.url = url
        self.visited = []

    async def goto(self, url, **_):
        self.visited.append(url)
        self.url = "https://www.linkedin.com/checkpoint/challenge/AgF123"

    async def bring_to_front(self):
        pass


@pytest.fixture
def server(monkeypatch):
    """A browser singleton that the module opens and closes, all in memory."""
    v.reset_for_testing()
    FakeViewer.instances.clear()
    calls = {"in_flight": 0, "closed": 0}
    page = FakePage("about:blank")
    manager = SimpleNamespace(page=page)
    drivers = v.drivers
    monkeypatch.setattr(drivers, "_browser", None)

    async def open_browser():
        drivers._browser = manager
        return manager

    async def close_browser():
        calls["closed"] += 1
        drivers._browser = None

    def started():
        calls["in_flight"] += 1

    def finished():
        calls["in_flight"] -= 1

    monkeypatch.setattr(v, "_browser_without_validation", open_browser)
    monkeypatch.setattr(v, "_require_viewer_host", lambda: None)
    monkeypatch.setattr(v, "LoginViewer", FakeViewer)
    monkeypatch.setattr(drivers, "close_browser", close_browser)
    monkeypatch.setattr(drivers, "note_call_started", started)
    monkeypatch.setattr(drivers, "note_activity", finished)
    monkeypatch.setenv(v.PUBLIC_URL_ENV, "https://verify.example")
    state = SimpleNamespace(account_restricted=True)
    monkeypatch.setattr(v, "get_bootstrap_state", lambda: state)
    yield SimpleNamespace(page=page, calls=calls, bootstrap=state)
    v.reset_for_testing()


async def test_open_shows_the_checkpoint_and_holds_the_browser(server):
    out = await v.open_verification(seconds=60)

    assert out["viewer_url"] == "https://verify.example/login.html#token=abc"
    assert out["state"] == "challenge"
    assert server.page.visited == ["https://www.linkedin.com/feed/"]
    # Held as a call in flight, so the idle timer cannot close it under the person.
    assert server.calls["in_flight"] == 1

    again = await v.open_verification(seconds=60)
    assert again["already_open"] is True
    assert len(FakeViewer.instances) == 1

    await v.finish_verification()


async def test_finish_while_still_challenged_keeps_the_restriction(server):
    await v.open_verification(seconds=60)
    out = await v.finish_verification()

    assert out["state"] == "challenge" and out["closed"] is True
    assert FakeViewer.instances[0].stopped
    assert server.calls == {"in_flight": 0, "closed": 1}
    assert server.bootstrap.account_restricted is True
    assert (await v.verification_status())["open"] is False


async def test_finish_after_the_check_clears_the_restriction(server):
    await v.open_verification(seconds=60)
    server.page.url = "https://www.linkedin.com/feed/"

    status = await v.verification_status()
    assert status["state"] == "ok" and status["open"] is True

    out = await v.finish_verification()
    assert out["state"] == "ok"
    assert server.bootstrap.account_restricted is False


async def test_status_waits_for_the_check_to_clear_without_navigating(
    server, monkeypatch
):
    await v.open_verification(seconds=60)
    polls = []

    async def sleep(_):
        polls.append(server.page.url)
        if len(polls) == 3:
            server.page.url = "https://www.linkedin.com/feed/"

    monkeypatch.setattr(v.asyncio, "sleep", sleep)
    out = await v.verification_status(wait_seconds=60)

    assert out["state"] == "ok" and len(polls) == 3
    assert server.page.visited == ["https://www.linkedin.com/feed/"]  # only the open
    await v.finish_verification()


async def test_the_viewer_closes_itself_when_it_expires(server):
    await v.open_verification(seconds=0)
    for _ in range(50):
        if FakeViewer.instances[0].stopped:
            break
        await asyncio.sleep(0.01)

    assert FakeViewer.instances[0].stopped
    assert server.calls["closed"] == 1
    assert (await v.verification_status())["open"] is False
