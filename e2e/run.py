"""End-to-end check against the real Apple TV: a real Firefox with Couchcast and uBlock Origin
installed opens a YouTube video, clicks the in-player cast button, then watches the helper's
log and player.json to see whether the TV session holds and where the TV is.

    uv run --project e2e python e2e/run.py                  # cast, pause Firefox (the default)
    uv run --project e2e python e2e/run.py --no-pause       # cast, leave Firefox playing
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
UBLOCK = ROOT / ".dev" / "ublock-origin.xpi"
HOME = Path.home() / ".config" / "couchcast"
LOG = HOME / "helper.log"
PLAYER = HOME / "player.json"
FIREFOX = "/Applications/Firefox.app/Contents/MacOS/firefox"
os.environ.setdefault("SE_AVOID_STATS", "true")
WATCH = re.compile(
    r"\[(host|player|airplay|cli)\]|Connection (closed|was lost)|Heartbeat \d+ failed|Feedback failed|tv says"
)


def say(message: str) -> None:
    print(f"[e2e {time.strftime('%H:%M:%S')}] {message}", flush=True)


def prepare_extension(pause_after_cast: bool) -> Path:
    """A copy of the extension with its settings pre-set: Marionette refuses to drive
    privileged pages, so the settings page can't be clicked through. No-ads mode by default,
    the helper permission granted up front, and the pause behavior under test."""
    copy = ROOT / ".dev" / "e2e-extension"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(EXTENSION, copy)
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["permissions"].append("nativeMessaging")
    manifest.pop("optional_permissions", None)
    (copy / "manifest.json").write_text(json.dumps(manifest, indent=2))
    store = copy / "lib" / "store.js"
    source = store.read_text()
    for old, new in (
        ("return mode === 'direct' ? 'direct' : 'youtube-app';", "return mode === 'youtube-app' ? 'youtube-app' : 'direct';"),
        ("return pauseAfterCast !== false;", f"return {'true' if pause_after_cast else 'false'};"),
    ):
        assert old in source, f"store.js no longer contains {old!r}; update the e2e harness"
        source = source.replace(old, new)
    store.write_text(source)
    say(f"test copy of the extension: no-ads mode, pause Firefox after casting = {pause_after_cast}")
    return copy


def launch(extension: Path, headless: bool, ublock: bool = True) -> webdriver.Firefox:
    options = Options()
    options.binary_location = FIREFOX
    for key, value in {"media.autoplay.default": 0, "media.autoplay.blocking_policy": 0}.items():
        options.set_preference(key, value)
    if headless:
        options.add_argument("-headless")
    t0 = time.monotonic()
    driver = webdriver.Firefox(options=options)
    driver.set_window_size(1600, 1000)
    if ublock:
        driver.install_addon(str(UBLOCK))
    driver.install_addon(str(extension), temporary=True)
    # The extension opens its settings tab on install; stay on the first tab.
    driver.switch_to.window(driver.window_handles[0])
    say(f"firefox up with {'uBlock Origin + ' if ublock else ''}Couchcast in {time.monotonic() - t0:.1f}s")
    return driver


def play_locally(driver: webdriver.Firefox, url: str) -> float:
    """Open the video and wait out any pre-roll ad (no ad blocker in the test profile)."""
    driver.get(url)

    def content_playing(d):
        s = d.execute_script(
            "const v = document.querySelector('#movie_player video');"
            "return v && { ready: v.readyState, t: v.currentTime, paused: v.paused, rate: v.playbackRate,"
            " ad: !!document.querySelector('#movie_player.ad-showing') }"
        )
        return s if s and s["ready"] >= 2 and not s["ad"] and not s["paused"] and s["t"] > 1 else None

    state = WebDriverWait(driver, 120, poll_frequency=1).until(content_playing)
    time.sleep(3)
    say(f"firefox playing the video itself: t={state['t']:.1f}s rate={state['rate']}")
    return state["t"]


def click_cast(driver: webdriver.Firefox) -> str:
    player = driver.find_element(By.ID, "movie_player")
    ActionChains(driver).move_to_element(player).perform()
    button = WebDriverWait(driver, 15).until(lambda d: d.find_element(By.CSS_SELECTOR, "#movie_player .couchcast-button"))
    say("clicking the in-player cast button")
    driver.execute_script("arguments[0].click()", button)
    return WebDriverWait(driver, 40).until(
        lambda d: d.execute_script(
            "const t = document.querySelector('#movie_player .couchcast-tip');"
            "return t && t.dataset.kind ? `${t.dataset.kind}: ${t.textContent}` : null"
        )
    )


def watch(seconds: int, log_offset: int) -> dict:
    last = None
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        info = json.loads(PLAYER.read_text()) if PLAYER.exists() else {}
        snapshot = (info.get("pid"), info.get("state"), round(info.get("position") or 0))
        if snapshot != last:
            say(f"player.json pid={snapshot[0]} state={snapshot[1]} position={snapshot[2]}s")
            last = snapshot
        time.sleep(1)
    text = LOG.read_bytes()
    lines = text[log_offset if len(text) >= log_offset else 0 :].decode(errors="replace").splitlines()
    seen = set()
    for line in lines:
        key = line[24:]
        if WATCH.search(line) and key not in seen:
            seen.add(key)
            print("   ", line[11:23], line[24:][:190])
    return json.loads(PLAYER.read_text()) if PLAYER.exists() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.youtube.com/watch?v=kYnwCx2SNYY&t=257")
    parser.add_argument("--pause", action=argparse.BooleanOptionalAction, default=True, help="pause Firefox after casting")
    parser.add_argument("--watch", type=int, default=20, help="seconds to watch the TV session")
    parser.add_argument("--headless", action="store_true")
    # A fresh profile's uBlock hasn't fetched its lists yet, and YouTube stalls playback.
    parser.add_argument("--ublock", action="store_true", help="install uBlock Origin in the test profile")
    args = parser.parse_args()

    driver = launch(prepare_extension(args.pause), args.headless, ublock=args.ublock)
    outcome = {}
    try:
        local_t = play_locally(driver, args.url)
        log_offset = LOG.stat().st_size if LOG.exists() else 0
        cast_at = time.time()
        say(f"cast result: {click_cast(driver)}")
        outcome = watch(args.watch, log_offset)
        local = driver.execute_script("const v = document.querySelector('#movie_player video'); return { t: v.currentTime, paused: v.paused }")
        say(f"firefox after: t={local['t']:.1f}s paused={local['paused']}")
        alive = outcome.get("state") in {"playing", "paused"}
        tv_now = None
        if outcome.get("position") is not None and outcome.get("positionAt"):
            running = (time.time() - outcome["positionAt"]) * (outcome.get("rate") or 1) if outcome.get("state") == "playing" else 0
            tv_now = outcome["position"] + running
        expected = local_t + (time.time() - cast_at) * (outcome.get("rate") or 1)
        in_place = tv_now is not None and abs(tv_now - expected) < 15
        say(f"TV position now ~{tv_now if tv_now is None else round(tv_now)}s, expected ~{expected:.0f}s (cast from {local_t:.0f}s)")
        ok = alive and in_place
        say(f"{'PASS' if ok else 'FAIL'}: session {'held' if alive else 'ended'} for {args.watch}s, "
            f"position {'carried over' if in_place else 'NOT carried over'}")
        return 0 if ok else 1
    finally:
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        subprocess.run(["uv", "run", "--project", str(ROOT / "helper"), "couchcast-helper", "stop"], cwd=ROOT, env=env)
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
