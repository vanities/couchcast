"""The extension's in-page behavior, checked in a real Firefox against a stand-in YouTube watch
page, so neither YouTube nor the TV is involved. Firefox's automation protocol (WebDriver BiDi)
answers every YouTube request with the page below and nothing goes out; the helper runs with an
empty home (no TV paired), so it only records what the extension asked for.

    uv run --project e2e python e2e/fake_youtube.py                # every scenario
    uv run --project e2e python e2e/fake_youtube.py switch reload  # some of them
"""

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from selenium.webdriver.support.ui import WebDriverWait

from run import cast_request, click_cast, hover_cast_button, launch, prepare_extension, reload_extension, say, wait_until_playing

FIRST, SECOND = "couchcastA1", "couchcastB2"
CLIP = Path(__file__).parent / "fixtures" / "clip.webm"
YOUTUBE_HOSTS = ["*://*.youtube.com/*", "*://youtube.com/*", "*://*.googlevideo.com/*", "*://*.ytimg.com/*", "*://*.ggpht.com/*"]

# Only what Couchcast touches: the player element and its controls, the getVideoData /
# getCurrentTime / getPlaybackRate API, and switching videos in place with pushState.
WATCH_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Stand-in YouTube</title></head>
<body style="background:#0f0f0f;color:#fff;font:14px sans-serif">
<div id="movie_player" class="html5-video-player" style="position:relative;width:640px;height:360px">
  <video autoplay muted playsinline style="width:100%;height:100%" src="data:video/webm;base64,__CLIP__"></video>
  <div class="ytp-chrome-bottom" style="position:absolute;bottom:0;right:0">
    <div class="ytp-right-controls">
      <button class="ytp-button ytp-settings-button">settings</button>
      <button class="ytp-button ytp-remote-button" style="display:none"></button>
      <button class="ytp-button ytp-fullscreen-button">fullscreen</button>
    </div>
  </div>
</div>
<div id="secondary"><a id="next" href="/watch?v=__SECOND__">Next video</a></div>
<script>
  const player = document.getElementById('movie_player');
  const video = player.querySelector('video');
  let current = new URL(location.href).searchParams.get('v');
  player.getVideoData = () => ({ video_id: current, list: null, isLive: false });
  player.getCurrentTime = () => video.currentTime;
  player.getPlaybackRate = () => video.playbackRate;
  document.getElementById('next').addEventListener('click', (event) => {
    event.preventDefault();
    current = new URL(event.currentTarget.href).searchParams.get('v');
    history.pushState({}, '', event.currentTarget.href);
    video.currentTime = 0;
    video.play();
  });
</script>
</body></html>"""


class StandInYouTube:
    def __init__(self, driver):
        self.page = WATCH_PAGE.replace("__CLIP__", base64.b64encode(CLIP.read_bytes()).decode()).replace("__SECOND__", SECOND)
        self.served: list[str] = []
        driver.network.add_request_handler(YOUTUBE_HOSTS, self.answer)

    def answer(self, request) -> None:
        self.served.append(request.url)
        if "/watch?" in request.url:
            request.provide_response(status=200, headers={"Content-Type": "text/html; charset=utf-8"}, body=self.page)
        else:
            request.provide_response(status=404, headers={"Content-Type": "text/plain"}, body="not part of the stand-in")


def open_video(driver, video_id: str, rate: float = 1.0) -> float:
    driver.get(f"https://www.youtube.com/watch?v={video_id}")
    if rate != 1.0:
        driver.execute_script("document.querySelector('#movie_player video').playbackRate = arguments[0]", rate)
    return wait_until_playing(driver, video_id)


def cast(driver, log: Path) -> dict:
    offset = log.stat().st_size if log.exists() else 0
    say(f"cast result: {click_cast(driver)}")
    request = cast_request(log, offset)
    say(f"extension sent {request}")
    return request


def check(name: str, ok: bool, detail: str) -> bool:
    say(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return ok


def scenario_cast(driver, home: Path, extension: Path) -> bool:
    local_t = open_video(driver, FIRST, rate=1.5)
    request = cast(driver, home / "helper.log")
    at = request.get("startSeconds") or 0
    ok = request.get("videoId") == FIRST and abs(at - local_t) < 10 and request.get("rate") == 1.5
    return check("cast", ok, f"sent {request.get('videoId')} at {at:.1f}s x{request.get('rate')}; page had {FIRST} at {local_t:.1f}s x1.5")


def scenario_switch(driver, home: Path, extension: Path) -> bool:
    """Firefox keeps a content script's sender.url from its first message (here the hover)."""
    open_video(driver, FIRST)
    hover_cast_button(driver)
    driver.execute_script("document.getElementById('next').click()")
    WebDriverWait(driver, 10).until(lambda d: d.current_url.endswith(SECOND))
    local_t = wait_until_playing(driver, SECOND)
    request = cast(driver, home / "helper.log")
    at = request.get("startSeconds") or 0
    ok = request.get("videoId") == SECOND and abs(at - local_t) < 10
    return check("switch", ok, f"sent {request.get('videoId')} at {at:.1f}s after switching {FIRST} -> {SECOND} in the page")


def scenario_catch_up(driver, home: Path, extension: Path) -> bool:
    """Coming back to a paused tab whose video is on the TV moves it to the TV's position."""
    open_video(driver, FIRST)
    tv_at = 300.0
    # The fake TV session needs a live pid; this script's will do.
    record = {"pid": os.getpid(), "state": "playing", "videoId": FIRST, "position": tv_at, "positionAt": time.time(), "rate": 1.0, "device": "Bedroom"}
    (home / "player.json").write_text(json.dumps(record))
    driver.execute_script("document.querySelector('#movie_player video').pause()")
    time.sleep(2.5)  # past the content script's 2s debounce
    driver.execute_script("window.dispatchEvent(new Event('focus'))")
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.querySelector('#movie_player video').currentTime") > tv_at - 1
        )
    except Exception:
        pass
    local = driver.execute_script("return document.querySelector('#movie_player video').currentTime")
    tip = driver.execute_script("return document.querySelector('#movie_player .couchcast-tip')?.textContent ?? ''")
    (home / "player.json").unlink()
    ok = abs(local - tv_at) < 5 and "Caught up" in tip
    return check("catch-up", ok, f"tab moved to {local:.1f}s (TV at ~{tv_at:.0f}s), tip {tip!r}")


def scenario_reload(driver, home: Path, extension: Path) -> bool:
    """After a reload the old copy's button no longer works; a new copy has to replace it."""
    open_video(driver, FIRST)
    hover_cast_button(driver)
    before = driver.execute_script("return document.querySelector('#movie_player .couchcast-button').dataset.loadedAt")
    reload_extension(driver, extension)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "const b = document.querySelectorAll('#movie_player .couchcast-button');"
                "return b.length === 1 && !!b[0].dataset.loadedAt && b[0].dataset.loadedAt !== arguments[0]",
                before,
            )
        )
    except Exception:
        return check("reload", False, "no new cast button after the reload; the old one stays and does nothing")
    request = cast(driver, home / "helper.log")
    return check("reload", request.get("videoId") == FIRST, f"the replaced button sent {request.get('videoId')}")


SCENARIOS = {"cast": scenario_cast, "switch": scenario_switch, "catch-up": scenario_catch_up, "reload": scenario_reload}


def main() -> int:
    wanted = sys.argv[1:] or list(SCENARIOS)
    unknown = [name for name in wanted if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario(s) {unknown}; choose from {list(SCENARIOS)}")
    home = Path(tempfile.mkdtemp(prefix="couchcast-fake-"))
    # Firefox hands its environment to the helper it starts: no paired TV, nothing reaches it.
    os.environ["COUCHCAST_HOME"] = str(home)
    say(f"helper home {home}")
    extension = prepare_extension(pause_after_cast=True)
    driver = launch(extension, headless=True, ublock=False, bidi=True)
    stand_in = StandInYouTube(driver)
    results = {}
    try:
        for name in wanted:
            try:
                results[name] = SCENARIOS[name](driver, home, extension)
            except Exception as error:
                results[name] = check(name, False, f"{type(error).__name__}: {error}")
    finally:
        driver.quit()
    not_found = [url for url in stand_in.served if "/watch?" not in url]
    say(f"answered {len(stand_in.served)} YouTube requests locally ({len(not_found)} with a 404)")
    passed = all(results.values())
    say(f"{'PASS' if passed else 'FAIL'}: " + ", ".join(f"{name} {'ok' if ok else 'FAILED'}" for name, ok in results.items()))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
