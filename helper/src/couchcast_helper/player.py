"""One background process per cast. It owns the AirPlay connection for the whole video, because
the Apple TV stops playing when the sender disconnects, and Firefox kills the messaging host as
soon as it answers."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time

from . import airplay, state

log = logging.getLogger("couchcast")


def current() -> dict:
    info = state.read_json(state.PLAYER)
    pid = info.get("pid")
    if not pid:
        return {}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {**info, "alive": False}
    return {**info, "alive": True}


def stop() -> bool:
    info = current()
    if not info.get("alive"):
        return False
    log.info("[player] stopping pid=%s", info["pid"])
    os.kill(info["pid"], signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.1)
        if not current().get("alive"):
            break
    return True


def spawn(url: str, position: float, title: str | None, device: dict) -> int:
    stop()
    args = [sys.executable, "-m", "couchcast_helper", "player", "--url", url, "--position", str(position), "--title", title or ""]
    # A new session keeps the player alive after Firefox reaps the messaging host's process group.
    with open(state.LOG, "a") as log_file:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, start_new_session=True)
    log.info("[player] spawned pid=%s device=%s position=%.0f", proc.pid, device["name"], position)
    return proc.pid


def wait_for_start(pid: int, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = state.read_json(state.PLAYER)
        if info.get("pid") == pid and info.get("state") in {"playing", "error", "ended"}:
            return info
        time.sleep(0.2)
    return {"pid": pid, "state": "starting"}


def run(url: str, position: float, title: str, device: dict) -> int:
    """Entry point of the background process."""
    record = {"pid": os.getpid(), "device": device["name"], "title": title, "position": position, "since": time.time()}
    state.write_json(state.PLAYER, {**record, "state": "starting"})
    if sys.platform == "darwin":
        # Idle sleep would drop the AirPlay connection mid-video.
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])

    def on_started() -> None:
        state.write_json(state.PLAYER, {**record, "state": "playing"})

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: task.cancel())
        storage = await airplay.open_storage(loop)
        conf = await airplay.find_tv(loop, storage, device)
        await airplay.play(loop, storage, conf, url, position, on_started)

    task = None
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(main())
        loop.run_until_complete(task)
        state.write_json(state.PLAYER, {**record, "state": "ended"})
        return 0
    except asyncio.CancelledError:
        log.info("[player] stopped by a newer cast or a stop request")
        state.write_json(state.PLAYER, {**record, "state": "stopped"})
        return 0
    except Exception as error:
        log.exception("[player] playback failed")
        state.write_json(state.PLAYER, {**record, "state": "error", "error": str(error) or type(error).__name__})
        return 1
    finally:
        loop.close()
