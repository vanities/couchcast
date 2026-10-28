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

ACTIVE = {"starting", "playing", "paused"}
# Push updates arrive several times a second; the position on disk only needs to be roughly fresh.
SAVE_EVERY_S = 3.0


def _is_ours(pid: int) -> bool:
    """Guards against signalling an unrelated process that reused a dead player's pid."""
    command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "couchcast_helper" in command or "couchcast-helper" in command


def current() -> dict:
    """The recorded session, plus whether it is alive and where the TV should be by now."""
    info = state.read_json(state.PLAYER)
    pid = info.get("pid")
    if not pid:
        return {}
    try:
        os.kill(pid, 0)
        alive = True
    except (ProcessLookupError, PermissionError):
        alive = False
    info["alive"] = alive
    position = info.get("position")
    if alive and info.get("state") == "playing" and position is not None and info.get("positionAt"):
        position += (time.time() - info["positionAt"]) * (info.get("rate") or 1.0)
    info["livePosition"] = position
    return info


def stop() -> bool:
    info = current()
    pid = info.get("pid")
    if not info.get("alive") or pid == os.getpid() or info.get("state") not in ACTIVE:
        return False
    if not _is_ours(pid):
        log.warning("[player] pid=%s from player.json is not a couchcast process; not signalling it", pid)
        return False
    log.info("[player] stopping pid=%s (%r on %s)", pid, info.get("title"), info.get("device"))
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.1)
        if not current().get("alive"):
            log.info("[player] pid=%s exited", pid)
            return True
    log.warning("[player] pid=%s still alive 3s after SIGTERM", pid)
    return True


class Session:
    """This process's entry in player.json, so other casts can find and stop it, and the
    extension can ask where the TV is."""

    def __init__(self, title: str | None, device: dict, position: float, video_id: str | None = None, rate: float = 1.0):
        stop()
        now = time.time()
        self.record = {
            "pid": os.getpid(),
            "device": device["name"],
            "title": title,
            "videoId": video_id,
            "position": position,
            "positionAt": now,
            "rate": rate,
            "since": now,
            "state": "starting",
        }
        self._saved_at = 0.0
        self._save()
        log.info("[player] session pid=%s video=%s at=%.0fs rate=%sx", os.getpid(), video_id, position, rate)

    def _save(self) -> None:
        on_disk = state.read_json(state.PLAYER)
        # A newer cast may already own the file while this one is shutting down.
        if on_disk.get("pid") not in (None, os.getpid()) and on_disk.get("state") in ACTIVE:
            log.debug("[player] pid=%s owns player.json now; not overwriting", on_disk.get("pid"))
            return
        state.write_json(state.PLAYER, self.record)
        self._saved_at = time.monotonic()

    def mark(self, new_state: str, **fields) -> None:
        changed = new_state != self.record["state"]
        if changed:
            log.info("[player] state %s -> %s %s", self.record["state"], new_state, fields or "")
        self.record.update(fields, state=new_state)
        if changed or time.monotonic() - self._saved_at >= SAVE_EVERY_S:
            self._save()

    def on_status(self, position: float | None, total: float | None, device_state: str | None) -> None:
        """Push update from the TV. "Seeking" and "Loading" keep the last steady state."""
        new_state = {"Playing": "playing", "Paused": "paused"}.get(device_state or "", self.record["state"])
        fields = {"duration": total} if total else {}
        if position is not None:
            fields.update(position=float(position), positionAt=time.time())
        log.debug("[player] tv says %s at=%s/%s", device_state, position, total)
        self.mark(new_state, **fields)


def spawn(url: str, position: float, title: str | None, device: dict, video_id: str | None = None, rate: float = 1.0) -> int:
    stop()
    args = [sys.executable, "-m", "couchcast_helper", "player", "--url", url, "--position", str(position), "--title", title or "", "--rate", str(rate)]
    if video_id:
        args += ["--video-id", video_id]
    # A new session keeps the player alive after Firefox reaps the messaging host's process group.
    with open(state.LOG, "a") as log_file:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, start_new_session=True)
    log.info("[player] spawned pid=%s device=%s video=%s position=%.0f rate=%sx", proc.pid, device["name"], video_id, position, rate)
    return proc.pid


def wait_for_start(pid: int, timeout: float = 15) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        info = state.read_json(state.PLAYER)
        if info.get("pid") == pid and info.get("state") in {"playing", "error", "ended"}:
            log.info("[player] pid=%s reached %s after %.1fs", pid, info["state"], time.monotonic() - t0)
            return info
        time.sleep(0.2)
    log.warning("[player] pid=%s still starting after %.0fs", pid, timeout)
    return {"pid": pid, "state": "starting"}


def run(url: str, position: float, title: str, device: dict, video_id: str | None = None, rate: float = 1.0) -> int:
    """Entry point of the background process."""
    session = Session(title, device, position, video_id, rate)
    if sys.platform == "darwin":
        # Idle sleep would drop the AirPlay connection mid-video.
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])

    signals_seen: list[str] = []

    def on_signal(sig: signal.Signals) -> None:
        signals_seen.append(sig.name)
        log.info("[player] got %s (parent pid %s, pgid %s, sid %s)", sig.name, os.getppid(), os.getpgid(0), os.getsid(0))
        task.cancel()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            loop.add_signal_handler(sig, on_signal, sig)
        log.info("[player] started pid=%s parent=%s pgid=%s sid=%s", os.getpid(), os.getppid(), os.getpgid(0), os.getsid(0))
        storage = await airplay.open_storage(loop)
        conf = await airplay.find_tv(loop, storage, device)
        await airplay.play(
            loop, storage, conf, url, position, lambda: session.mark("playing"), on_status=session.on_status, rate=rate
        )

    task = None
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(main())
        loop.run_until_complete(task)
        session.mark("ended")
        return 0
    except asyncio.CancelledError:
        if signals_seen:
            log.info("[player] stopped after %s", "+".join(signals_seen))
        else:
            log.warning("[player] playback was cancelled from inside pyatv", exc_info=True)
        session.mark("stopped")
        return 0
    except Exception as error:
        log.exception("[player] playback failed")
        session.mark("error", error=str(error) or type(error).__name__)
        return 1
    finally:
        loop.close()
        log.info("[player] exiting pid=%s", os.getpid())
