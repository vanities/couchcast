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

from . import state

log = logging.getLogger("couchcast")

ACTIVE = {"starting", "playing", "paused"}
# Push updates arrive several times a second; the position on disk only needs to be roughly fresh.
SAVE_EVERY_S = 3.0
# A player can run for hours; this shows what it costs without turning on debug logging.
USAGE_EVERY_S = 600.0


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

    def started(self) -> None:
        """play_url has run for a few seconds without failing. The TV's own events say more, so
        this only moves a session the TV hasn't reported on yet."""
        if self.record["state"] == "starting":
            self.mark("playing")
        else:
            log.debug("[player] start signal ignored; the TV already reported %s", self.record["state"])

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
    if sys.platform == "darwin" and os.environ.get("COUCHCAST_SPAWN") != "direct":
        return _spawn_via_launchd(args, device)
    # A new session keeps the player alive after Firefox reaps the messaging host's process group.
    with open(state.LOG, "a") as log_file:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, start_new_session=True)
    log.info("[player] spawned pid=%s device=%s video=%s position=%.0f rate=%sx", proc.pid, device["name"], video_id, position, rate)
    return proc.pid


LAUNCHD_PREFIX = "com.couchcast.player."


def _spawn_via_launchd(args: list[str], device: dict) -> int:
    """macOS treats anything started under Firefox as Firefox, even in its own session, and the
    Apple TV dropped every AirPlay session from such a player about 4 seconds in, while the same
    player started from a terminal or by launchd held (e2e runs A/B vs C, 2026-10-28). So launchd
    starts it. `launchctl submit` jobs restart when they exit, which run() guards against."""
    _cleanup_launchd_jobs()
    label = f"{LAUNCHD_PREFIX}{int(time.time() * 1000)}"
    t0 = time.monotonic()
    command = ["launchctl", "submit", "-l", label, "-o", str(state.LOG), "-e", str(state.LOG), "--", *args, "--launchd-label", label]
    subprocess.run(command, check=True, capture_output=True)
    for _ in range(50):
        pid = _launchd_pid(label)
        if pid:
            log.info("[player] launchd started pid=%s label=%s device=%s in %.1fs", pid, label, device["name"], time.monotonic() - t0)
            return pid
        time.sleep(0.1)
    raise RuntimeError(f"launchd didn't start the player ({label})")


def _launchd_pid(label: str) -> int | None:
    listing = subprocess.run(["launchctl", "list", label], capture_output=True, text=True).stdout
    return next((int(line.split("=")[1].strip(" ;")) for line in listing.splitlines() if '"PID"' in line), None)


def _cleanup_launchd_jobs() -> None:
    """Finished players stay loaded in launchd (they don't restart); unload them."""
    listing = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].startswith(LAUNCHD_PREFIX) and parts[0] == "-":
            subprocess.run(["launchctl", "remove", parts[2]], capture_output=True)
            (state.HOME / "launchd" / f"{parts[2]}.started").unlink(missing_ok=True)
            log.debug("[player] unloaded finished launchd job %s", parts[2])


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


def run(
    url: str, position: float, title: str, device: dict, video_id: str | None = None, rate: float = 1.0, launchd_label: str | None = None
) -> int:
    """Entry point of the background process."""
    marker = state.HOME / "launchd" / f"{launchd_label}.started" if launchd_label else None
    if marker and marker.exists():
        # launchd restarts submitted jobs; a player that already ran must not replay the video.
        log.warning("[player] launchd restarted %s after it ran; retiring the job", launchd_label)
        marker.unlink(missing_ok=True)
        subprocess.run(["launchctl", "remove", launchd_label])
        return 0
    if marker:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    try:
        return _run(url, position, title, device, video_id, rate)
    finally:
        if marker:
            log.info("[player] removing launchd job %s", launchd_label)
            marker.unlink(missing_ok=True)
            subprocess.run(["launchctl", "remove", launchd_label])


async def _report_usage(session: Session, since: float) -> None:
    last_cpu, _ = state.usage()
    last_at = time.monotonic()
    while True:
        await asyncio.sleep(USAGE_EVERY_S)
        cpu, peak_mb = state.usage()
        now = time.monotonic()
        log.info(
            "[player] usage up=%s state=%s cpu=+%.1fs over %s (%.2f%% of a core) peak=%.0fMB",
            state.elapsed(now - since),
            session.record["state"],
            cpu - last_cpu,
            state.elapsed(now - last_at),
            (cpu - last_cpu) / (now - last_at) * 100,
            peak_mb,
        )
        last_cpu, last_at = cpu, now


def _run(url: str, position: float, title: str, device: dict, video_id: str | None, rate: float) -> int:
    # Imported here, not at the top: pyatv is most of a messaging host's startup time, and the
    # host imports this module for status and stop, which never touch the TV.
    from . import airplay

    t_start = time.monotonic()
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
        reporter = asyncio.ensure_future(_report_usage(session, t_start))
        try:
            storage = await airplay.open_storage(loop)
            conf = await airplay.find_tv(loop, storage, device)
            await airplay.play(loop, storage, conf, url, position, session.started, on_status=session.on_status, rate=rate)
        finally:
            reporter.cancel()
            await asyncio.gather(reporter, return_exceptions=True)

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
            # pyatv closes everything when the TV stops answering its remote-control heartbeat.
            log.warning("[player] pyatv closed the connection and cancelled playback; the TV stopped answering", exc_info=True)
        session.mark("stopped")
        return 0
    except Exception as error:
        log.exception("[player] playback failed")
        session.mark("error", error=str(error) or type(error).__name__)
        return 1
    finally:
        loop.close()
        cpu, peak_mb = state.usage()
        up = time.monotonic() - t_start
        log.info(
            "[player] exiting pid=%s after %s: cpu %.1fs (%.2f%% of a core), peak %.0fMB",
            os.getpid(),
            state.elapsed(up),
            cpu,
            cpu / up * 100 if up > 0 else 0,
            peak_mb,
        )
