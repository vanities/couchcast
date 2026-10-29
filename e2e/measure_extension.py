"""What Couchcast costs inside Firefox, measured in a real headless Firefox against the stand-in
YouTube page from fake_youtube.py, so neither YouTube nor the TV is involved.

    uv run --project e2e python e2e/measure_extension.py                  # 3 on/off pairs, 65s each
    uv run --project e2e python e2e/measure_extension.py --pairs 5 --seconds 65

The page gets a YouTube-sized DOM and swaps a block of nodes every frame, the most often the
button upkeep can run. Windows alternate between Couchcast enabled and disabled; each samples
the tab's content-process CPU from Firefox's own process stats, and enabled windows also catch
the extension's own once-a-minute upkeep summary from the console. Afterwards it idles to see
whether Firefox has suspended the background page, and reports the extension process's memory.
"""

import argparse
import os
import re
import statistics
import tempfile
import time
from pathlib import Path

from fake_youtube import FIRST, StandInYouTube, open_video
from run import launch, prepare_extension, say

ADDON_ID = "couchcast@vanities"
# Firefox suspends an idle event page after extensions.background.idle.timeout (30s by default).
IDLE_CHECK_S = 45

CHURN = """
const filler = document.createElement('div');
for (let i = 0; i < 2000; i++) {
  const card = document.createElement('div');
  card.innerHTML = '<a href="#"><img alt=""><span>title</span><span>channel</span></a>';
  filler.append(card);
}
document.body.append(filler);
const box = document.createElement('div');
document.body.append(box);
window.__frames = 0;
(function tick() {
  box.replaceChildren(...Array.from({ length: 20 }, () => document.createElement('span')));
  window.__frames++;
  requestAnimationFrame(tick);
})();
return document.getElementsByTagName('*').length;
"""

PROCESSES = """
const done = arguments[arguments.length - 1];
ChromeUtils.requestProcInfo().then(
  (info) => done(info.children.map((c) => ({
    pid: c.pid, type: c.type, cpuMs: c.cpuTime / 1e6, memoryMb: c.memory / 1e6,
    windows: (c.windows || []).map((w) => (w.documentURI ? w.documentURI.spec : '')),
  }))),
  (error) => done({ error: String(error) }),
);
"""

# The extension process also hosts Firefox's built-in add-ons, each with a background page, so
# only Couchcast's own Extension object can say whether its page is running.
BACKGROUND = """
const [id, done] = arguments;
const extension = WebExtensionPolicy.getByID(id)?.extension;
ChromeUtils.requestProcInfo().then(
  (info) => done({
    state: extension?.backgroundState ?? 'unknown',
    processMb: (info.children.find((c) => c.type === 'extension')?.memory ?? 0) / 1e6,
  }),
  (error) => done({ state: 'unknown', error: String(error) }),
);
"""

SET_ENABLED = """
const [id, enabled, done] = arguments;
const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
AddonManager.getAddonByID(id)
  .then((addon) => (enabled ? addon.enable() : addon.disable()))
  .then(() => done('ok'), (error) => done(String(error)));
"""


def processes(driver) -> list[dict]:
    with driver.context(driver.CONTEXT_CHROME):
        result = driver.execute_async_script(PROCESSES)
    if isinstance(result, dict):
        raise RuntimeError(f"process stats failed: {result.get('error')}")
    return result


def tab_process(driver) -> dict:
    return next(p for p in processes(driver) if any("youtube.com/watch" in uri for uri in p["windows"]))


def background(driver) -> dict:
    with driver.context(driver.CONTEXT_CHROME):
        return driver.execute_async_script(BACKGROUND, ADDON_ID)


def set_enabled(driver, enabled: bool) -> None:
    t0 = time.monotonic()
    with driver.context(driver.CONTEXT_CHROME):
        result = driver.execute_async_script(SET_ENABLED, ADDON_ID, enabled)
    if result != "ok":
        raise RuntimeError(f"couldn't {'enable' if enabled else 'disable'} Couchcast: {result}")
    say(f"couchcast {'enabled' if enabled else 'disabled'} in {(time.monotonic() - t0) * 1000:.0f}ms")


def measure_window(driver, enabled: bool, seconds: float) -> dict:
    set_enabled(driver, enabled)
    open_video(driver, FIRST)
    driver.execute_script("document.querySelector('#movie_player video').pause()")
    elements = driver.execute_script(CHURN)
    time.sleep(2)
    before = tab_process(driver)
    frames_before = driver.execute_script("return window.__frames")
    t0 = time.monotonic()
    time.sleep(seconds)
    after = tab_process(driver)
    frames = driver.execute_script("return window.__frames") - frames_before
    wall = time.monotonic() - t0
    if after["pid"] != before["pid"]:
        raise RuntimeError("the tab changed processes mid-window")
    result = {
        "enabled": enabled,
        "cpu_pct": (after["cpuMs"] - before["cpuMs"]) / (wall * 1000) * 100,
        "fps": frames / wall,
        "elements": elements,
        "tab_mb": after["memoryMb"],
    }
    say(
        f"couchcast {'on ' if enabled else 'off'}: tab process {result['cpu_pct']:.1f}% of a core, "
        f"{result['fps']:.1f} frames/s, {elements} elements, {result['tab_mb']:.0f}MB, over {wall:.0f}s"
    )
    return result


def summarize(label: str, values: list[float], unit: str) -> str:
    return f"{label} median {statistics.median(values):.2f}{unit} (range {min(values):.2f}-{max(values):.2f}, n={len(values)})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=65.0, help="window length; 60s+ catches the upkeep summary")
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="couchcast-measure-"))
    # Firefox hands its environment to the helper it starts: no paired TV, nothing reaches it.
    os.environ["COUCHCAST_HOME"] = str(home)
    extension = prepare_extension(pause_after_cast=True)
    # Full-precision timers, so the extension's own upkeep timer isn't rounded to 1ms.
    driver = launch(extension, headless=True, ublock=False, bidi=True, system_access=True, prefs={"privacy.reduceTimerPrecision": False})
    StandInYouTube(driver)
    upkeep: list[str] = []
    driver.script.add_console_message_handler(lambda entry: upkeep.append(entry.text) if "button upkeep" in (entry.text or "") else None)
    results = []
    try:
        for _ in range(args.pairs):
            for enabled in (True, False):
                results.append(measure_window(driver, enabled, args.seconds))
        set_enabled(driver, True)
        open_video(driver, FIRST)
        busy = background(driver)
        say(f"idling {IDLE_CHECK_S}s with Couchcast enabled to see whether its background page is suspended")
        time.sleep(IDLE_CHECK_S)
        idle = background(driver)
    finally:
        driver.quit()

    on = [r["cpu_pct"] for r in results if r["enabled"]]
    off = [r["cpu_pct"] for r in results if not r["enabled"]]
    say(summarize("tab CPU with Couchcast on: ", on, "%") + "; " + summarize("off:", off, "%"))
    say(f"difference of medians: {statistics.median(on) - statistics.median(off):+.2f} points of a core")
    say(summarize("frames/s on: ", [r["fps"] for r in results if r["enabled"]], "") + "; " + summarize("off:", [r["fps"] for r in results if not r["enabled"]], ""))
    for line in upkeep:
        match = re.search(r"(\d+) checks in (\d+)s, ([\d.]+)ms total", line)
        if match:
            checks, secs, total = int(match[1]), int(match[2]), float(match[3])
            say(f"extension's own timer: {checks} checks in {secs}s, {total:.1f}ms total = {total / checks * 1000:.0f}us per check, {total / (secs * 1000) * 100:.3f}% of a core")
    if not upkeep:
        say("no upkeep summary reached the console (windows shorter than 60s, or BiDi doesn't relay content-script console messages)")
    say(
        f"background page: {busy['state']} after enabling, {idle['state']} after {IDLE_CHECK_S}s idle; extension process "
        f"{busy['processMb']:.0f}MB -> {idle['processMb']:.0f}MB (it also hosts Firefox's built-in add-ons)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
