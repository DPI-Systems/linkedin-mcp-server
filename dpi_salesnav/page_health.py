"""Whether a lead page is busy, stuck, or just waiting, and a way out of a stuck one.

Measured on account 2: a lead page whose renderer stops answering. ``evaluate``
times out, a screenshot comes back empty, and ``goto`` cannot leave the page.
Two readings tell a long-running script from a renderer that is idle but
blocked: the CPU the renderer processes used during the wait, read from
``/proc`` so it works while the page does not answer, and the page's own long
tasks, read once it answers again.
"""

import asyncio
import logging
import os
from typing import Any

from linkedin_mcp_server.drivers import browser as drivers
from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.core.throttle import watch_responses

logger = logging.getLogger(__name__)

_CLOSE_TIMEOUT = 10.0
# For goto, reload and the waits for a load. The server's default of 5 s is
# for reading a page that is already there; measured on dpi-lab under load,
# Sales Navigator pages often take longer to start, on both accounts.
NAVIGATION_TIMEOUT_MS = 45_000


def set_timeouts(page: Any) -> None:
    """Give a page the navigation timeout; reading keeps the server default."""
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)


# What the page's own performance timeline kept, read through buffered
# observers so nothing has to be injected before the page loads: its longest
# tasks, its longest animation frames with the script that held each one, and
# every change of its visibility.
LONG_TASKS = """async () => {
    const read = type => new Promise(done => {
        try {
            const seen = [];
            const observer = new PerformanceObserver(list => {
                seen.push(...list.getEntries());
            });
            observer.observe({type, buffered: true});
            setTimeout(() => {
                seen.push(...observer.takeRecords());
                observer.disconnect();
                done(seen);
            }, 50);
        } catch (e) {
            done(null);
        }
    });
    const top = entries => [...entries]
        .sort((a, b) => b.duration - a.duration).slice(0, 5);
    const tasks = await read('longtask');
    const frames = await read('long-animation-frame');
    const shown = await read('visibility-state');
    return {
        count: tasks ? tasks.length : null,
        total_ms: tasks ? Math.round(tasks.reduce((s, t) => s + t.duration, 0)) : null,
        longest: tasks ? top(tasks).map(t => ({
            duration_ms: Math.round(t.duration),
            start_ms: Math.round(t.startTime),
        })) : null,
        frames: frames ? top(frames).map(f => {
            const script = [...(f.scripts || [])]
                .sort((a, b) => b.duration - a.duration)[0];
            return {
                duration_ms: Math.round(f.duration),
                blocking_ms: Math.round(f.blockingDuration || 0),
                start_ms: Math.round(f.startTime),
                script: script ? {
                    duration_ms: Math.round(script.duration),
                    invoker: String(script.invoker || '').slice(0, 120),
                    source: String(script.sourceURL || '').split('?')[0].slice(-120),
                } : null,
            };
        }) : null,
        visibility: shown ? shown.map(v => ({
            state: v.name, at_ms: Math.round(v.startTime),
        })) : null,
    };
}"""


def renderer_cpu() -> dict[int, float]:
    """CPU seconds used so far by each Chromium renderer process (Linux only)."""
    try:
        tick = os.sysconf("SC_CLK_TCK")
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except (OSError, ValueError, AttributeError):
        return {}
    used: dict[int, float] = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"--type=renderer" not in f.read():
                    continue
            with open(f"/proc/{pid}/stat") as f:
                # The command name can hold spaces; fields resume after ')'.
                fields = f.read().rsplit(")", 1)[1].split()
            used[pid] = (int(fields[11]) + int(fields[12])) / tick
        except (OSError, IndexError, ValueError):
            continue
    return used


def cpu_since(before: dict[int, float], wall_seconds: float) -> dict[str, Any]:
    """The renderers that used the most CPU since *before*, busiest first."""
    now = renderer_cpu()
    if not now:
        return {"available": False}
    spent = sorted(
        ((pid, now[pid] - before.get(pid, 0.0)) for pid in now),
        key=lambda item: item[1],
        reverse=True,
    )
    wall = max(wall_seconds, 0.001)
    return {
        "wall_s": round(wall_seconds, 1),
        "renderers": len(now),
        "busiest": [
            {"pid": pid, "cpu_s": round(cpu, 1), "share": round(cpu / wall, 2)}
            for pid, cpu in spent[:3]
        ],
    }


async def replace_page(old: Any) -> Any:
    """Open a fresh page for the server's browser and close the stuck one.

    Navigating away needs the old renderer to let go of its document, which a
    stuck one does not. The new page takes the old one's place for every later
    tool call, with the same default timeout, crash hook and throttle watch.
    """
    manager = drivers._browser
    context = old.context
    new = await context.new_page()
    new.set_default_timeout(get_config().browser.default_timeout)
    set_timeouts(new)
    if manager is not None and getattr(manager, "_page", None) is old:
        new.on("crash", manager._note_page_crash)
        manager._page = new
        manager._page_crashed = False
    watch_responses(new)
    try:
        await asyncio.wait_for(old.close(run_before_unload=False), _CLOSE_TIMEOUT)
    except Exception:
        logger.warning("The stuck lead page did not close", exc_info=True)
    logger.warning("Replaced a lead page that stopped answering")
    return new
