"""End-to-end check in a real Firefox with Couchcast installed: open a YouTube video, click the
in-player cast button, then check what the extension sent to the helper and, against the real
Apple TV, whether the TV session holds and where the TV is (from the helper's log and player.json).

Every run is an automated YouTube visit. Runs close together trip YouTube's bot check, which then
blocks the helper's yt-dlp on the same network as well, so space them out.

    uv run --project e2e python e2e/run.py                   # cast, pause Firefox (the default)
    uv run --project e2e python e2e/run.py --no-pause        # cast, leave Firefox playing
    uv run --project e2e python e2e/run.py --next            # switch to a related video first
    uv run --project e2e python e2e/run.py --next --dry-run  # same, and nothing reaches the TV
    uv run --project e2e python e2e/run.py --reload --dry-run  # reload the extension, then cast
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
UBLOCK = ROOT / ".dev" / "ublock-origin.xpi"
UBLOCK_ID = "uBlock0@raymondhill.net"
PROFILE = ROOT / ".dev" / "e2e-profile"
UBLOCK_FIRST_FETCH = 60
HOME = Path.home() / ".config" / "couchcast"
FIREFOX = "/Applications/Firefox.app/Contents/MacOS/firefox"
os.environ.setdefault("SE_AVOID_STATS", "true")
WATCH = re.compile(
    r"\[(host|player|airplay|cli)\]|Connection (closed|was lost)|Heartbeat \d+ failed|Feedback failed|tv says"
)
RELATED = "#secondary a[href*='/watch?v='], #related a[href*='/watch?v=']"


def say(message: str) -> None:
    print(f"[e2e {time.strftime('%H:%M:%S')}] {message}", flush=True)


def video_id_of(url: str | None) -> str | None:
    return parse_qs(urlparse(url or "").query).get("v", [None])[0]


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


def forget_websites(profile: Path) -> None:
    """Drop the test profile's cookies and site storage and keep the extensions' storage. With the
    earlier test visits' cookies kept, YouTube's player failed with "Something went wrong. Refresh
    or try again later." (2026-10-28), likely an early stage of its bot check."""
    paths = [profile / "cookies.sqlite", profile / "cookies.sqlite-wal", *(profile / "storage" / "default").glob("http*")]
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def launch(extension: Path, headless: bool, ublock: bool, bidi: bool = False) -> webdriver.Firefox:
    options = Options()
    options.binary_location = FIREFOX
    options.enable_bidi = bidi
    # media.volume_scale silences everything the test Firefox plays, ads included.
    for key, value in {"media.autoplay.default": 0, "media.autoplay.blocking_policy": 0, "media.volume_scale": "0.0"}.items():
        options.set_preference(key, value)
    if headless:
        options.add_argument("-headless")
    if ublock:
        # Kept between runs so uBlock's filter lists stay current. The lists inside the xpi date
        # from its release, and with outdated lists YouTube stalls ("Experiencing interruptions?").
        PROFILE.mkdir(parents=True, exist_ok=True)
        forget_websites(PROFILE)
        options.add_argument("-profile")
        options.add_argument(str(PROFILE))
    t0 = time.monotonic()
    driver = webdriver.Firefox(options=options)
    driver.set_window_size(1600, 1000)
    if ublock and not (PROFILE / "extensions" / f"{UBLOCK_ID}.xpi").exists():
        driver.install_addon(str(UBLOCK))
        say(f"installed uBlock Origin in {PROFILE.relative_to(ROOT)}; giving it {UBLOCK_FIRST_FETCH}s to fetch current filter lists")
        time.sleep(UBLOCK_FIRST_FETCH)
    driver.install_addon(str(extension), temporary=True)
    # The extension opens its settings tab on install; stay on the first tab.
    driver.switch_to.window(driver.window_handles[0])
    say(f"firefox up with {'uBlock Origin + ' if ublock else ''}Couchcast in {time.monotonic() - t0:.1f}s")
    return driver


def wait_until_playing(driver: webdriver.Firefox, video_id: str) -> float:
    """Wait until the player is playing this video itself, past any pre-roll ad."""
    ad_polls = 0
    last = {}

    def content_playing(d):
        nonlocal ad_polls, last
        s = last = d.execute_script(
            "const p = document.getElementById('movie_player'); const v = p && p.querySelector('video');"
            "return v && { id: p.getVideoData?.().video_id, ready: v.readyState, t: v.currentTime, paused: v.paused,"
            " rate: v.playbackRate, ad: p.classList.contains('ad-showing') }"
        )
        ad_polls += bool(s and s["ad"])
        ready = s and s["id"] == video_id and s["ready"] >= 2 and not s["ad"] and not s["paused"] and s["t"] > 1
        return s if ready else None

    try:
        state = WebDriverWait(driver, 120, poll_frequency=1).until(content_playing)
    except TimeoutException:
        text = driver.execute_script("return document.body.innerText")
        shot = ROOT / ".dev" / "e2e-failure.png"
        driver.save_screenshot(str(shot))
        say(f"never started: url={driver.current_url} last player state={last} screenshot={shot}")
        if "not a bot" in text:
            raise RuntimeError(
                "YouTube is showing its bot check (Sign in to confirm you're not a bot). It blocks the helper's "
                "yt-dlp on this network too, so stop running tests until it clears."
            ) from None
        stalled = " (YouTube: Experiencing interruptions?)" if "Experiencing interruptions" in text else ""
        raise RuntimeError(f"{video_id} never started playing{stalled}") from None
    time.sleep(3)
    say(f"firefox playing {video_id} itself: t={state['t']:.1f}s rate={state['rate']} ads={f'~{ad_polls}s' if ad_polls else 'none'}")
    return state["t"]


def hover_cast_button(driver: webdriver.Firefox) -> None:
    """Point at the cast button until its label arrives from the background, as a person's mouse
    does on the way to it."""
    player = driver.find_element(By.ID, "movie_player")
    ActionChains(driver).move_to_element(player).perform()
    button = WebDriverWait(driver, 15).until(lambda d: d.find_element(By.CSS_SELECTOR, "#movie_player .couchcast-button"))
    ActionChains(driver).move_to_element(button).perform()
    label = WebDriverWait(driver, 10).until(
        lambda d: d.execute_script(
            "const t = document.querySelector('#movie_player .couchcast-tip');"
            "return t && t.textContent !== 'Cast to TV' ? t.textContent : null"
        )
    )
    say(f"hovered the cast button: {label!r}")


def switch_to_related(driver: webdriver.Firefox) -> str:
    """Click the first related video. YouTube swaps the video inside the same page, so the
    content script keeps running in a document that was loaded for the first video. Hover the
    cast button first so the content script has already talked to the background, the way it
    has in a tab someone has been using."""
    hover_cast_button(driver)
    current = video_id_of(driver.current_url)

    def first_related(d):
        for link in d.find_elements(By.CSS_SELECTOR, RELATED):
            href = link.get_attribute("href") or ""
            target = video_id_of(href)
            if target and target != current and "list=" not in href and link.is_displayed():
                return link, target
        return None

    link, target = WebDriverWait(driver, 30).until(first_related)
    driver.execute_script("window.couchcastE2eMark = true")
    say(f"switching videos inside YouTube: {current} -> {target}")
    driver.execute_script("arguments[0].click()", link)
    WebDriverWait(driver, 30).until(lambda d: video_id_of(d.current_url) == target)
    assert driver.execute_script("return window.couchcastE2eMark === true"), "the page reloaded instead of switching videos in place"
    return target


def reload_extension(driver: webdriver.Firefox, extension: Path) -> None:
    """Install the extension over itself, as about:debugging's Reload or an update does, with
    the YouTube tab left open."""
    tab = driver.current_window_handle
    driver.install_addon(str(extension), temporary=True)
    time.sleep(3)
    driver.switch_to.window(tab)
    buttons = driver.execute_script("return document.querySelectorAll('#movie_player .couchcast-button').length")
    say(f"reloaded the extension with the tab open; cast buttons in the player now: {buttons}")


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
        ),
        message="the cast button showed no result within 40s",
    )


def cast_request(log: Path, offset: int) -> dict:
    """The cast request as the helper logged it on arrival."""
    text = log.read_bytes()[offset:].decode(errors="replace") if log.exists() else ""
    found = re.findall(r"\[host\] request type=cast (\{.*\})", text)
    return ast.literal_eval(found[-1]) if found else {}


def watch(seconds: int, log: Path, player_file: Path, log_offset: int) -> dict:
    last = None
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        info = json.loads(player_file.read_text()) if player_file.exists() else {}
        snapshot = (info.get("pid"), info.get("state"), round(info.get("position") or 0))
        if snapshot != last:
            say(f"player.json pid={snapshot[0]} state={snapshot[1]} position={snapshot[2]}s")
            last = snapshot
        time.sleep(1)
    text = log.read_bytes()
    lines = text[log_offset if len(text) >= log_offset else 0 :].decode(errors="replace").splitlines()
    seen = set()
    for line in lines:
        key = line[24:]
        if WATCH.search(line) and key not in seen:
            seen.add(key)
            print("   ", line[11:23], line[24:][:190])
    return json.loads(player_file.read_text()) if player_file.exists() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.youtube.com/watch?v=kYnwCx2SNYY&t=257")
    parser.add_argument("--next", action="store_true", help="switch to a related video inside YouTube, then cast that one")
    parser.add_argument("--dry-run", action="store_true", help="only check what the extension sends; nothing reaches the TV")
    parser.add_argument("--reload", action="store_true", help="reload the extension with the tab open, then cast")
    parser.add_argument("--pause", action=argparse.BooleanOptionalAction, default=True, help="pause Firefox after casting")
    parser.add_argument("--watch", type=int, default=20, help="seconds to watch the TV session")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--ublock", action=argparse.BooleanOptionalAction, default=True, help="block ads with uBlock Origin")
    args = parser.parse_args()

    home = HOME
    if args.dry_run:
        # Firefox hands its environment to the helper it starts, so an empty helper home means no
        # paired TV: the helper logs the request, answers "not paired", and the TV is left alone.
        home = Path(tempfile.mkdtemp(prefix="couchcast-e2e-"))
        os.environ["COUCHCAST_HOME"] = str(home)
        say(f"dry run: helper home is {home}, nothing will reach the TV")
    log, player_file = home / "helper.log", home / "player.json"

    extension = prepare_extension(args.pause)
    driver = launch(extension, args.headless, ublock=args.ublock)
    try:
        driver.get(args.url)
        expected = video_id_of(args.url)
        local_t = wait_until_playing(driver, expected)
        if args.next:
            expected = switch_to_related(driver)
            local_t = wait_until_playing(driver, expected)
        if args.reload:
            reload_extension(driver, extension)
        log_offset = log.stat().st_size if log.exists() else 0
        cast_at = time.time()
        say(f"cast result: {click_cast(driver)}")
        request = cast_request(log, log_offset)
        sent_at = request.get("startSeconds")
        say(f"extension sent video={request.get('videoId')} at={sent_at} rate={request.get('rate')}; firefox had {expected} at {local_t:.0f}s")
        right_video = request.get("videoId") == expected
        sent_position = sent_at is not None and abs(sent_at - local_t) < 15
        if args.dry_run:
            ok = right_video and sent_position
            say(f"{'PASS' if ok else 'FAIL'}: {'right' if right_video else 'WRONG'} video, "
                f"position {'carried over' if sent_position else 'NOT carried over'}")
            return 0 if ok else 1

        outcome = watch(args.watch, log, player_file, log_offset)
        local = driver.execute_script("const v = document.querySelector('#movie_player video'); return { t: v.currentTime, paused: v.paused }")
        say(f"firefox after: t={local['t']:.1f}s paused={local['paused']}")
        alive = outcome.get("state") in {"playing", "paused"}
        on_tv = outcome.get("videoId") == expected
        tv_now = None
        if outcome.get("position") is not None and outcome.get("positionAt"):
            running = (time.time() - outcome["positionAt"]) * (outcome.get("rate") or 1) if outcome.get("state") == "playing" else 0
            tv_now = outcome["position"] + running
        expected_at = local_t + (time.time() - cast_at) * (outcome.get("rate") or 1)
        in_place = tv_now is not None and abs(tv_now - expected_at) < 15
        say(f"TV has {outcome.get('videoId')} at ~{tv_now if tv_now is None else round(tv_now)}s, expected {expected} at ~{expected_at:.0f}s (cast from {local_t:.0f}s)")
        ok = right_video and on_tv and alive and in_place
        say(f"{'PASS' if ok else 'FAIL'}: {'right' if right_video and on_tv else 'WRONG'} video, session {'held' if alive else 'ended'} "
            f"for {args.watch}s, position {'carried over' if in_place else 'NOT carried over'}")
        return 0 if ok else 1
    finally:
        if not args.dry_run:
            env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
            subprocess.run(["uv", "run", "--project", str(ROOT / "helper"), "couchcast-helper", "stop"], cwd=ROOT, env=env)
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
