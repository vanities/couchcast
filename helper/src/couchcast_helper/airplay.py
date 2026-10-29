import asyncio
import logging
import os
import time
from collections.abc import Callable

import pyatv
from pyatv.const import OperatingSystem, Protocol
from pyatv.interface import PairingHandler
from pyatv.protocols.raop.protocols import airplayv2
from pyatv.storage.file_storage import FileStorage

from . import state

log = logging.getLogger("couchcast")

# (position, duration, state) from the TV; any of them can be None. state is "Playing", "Paused",
# or another capitalized playbackState name ("Loading", "Stopped").
StatusCallback = Callable[[float | None, float | None, str | None], None]

# pyatv's AirPlay 2 URL playback (the pinned commit) starts every video at 0:00 and at 1x, and
# drops the TV's position reports. Two wrappers fix that until it's upstream:
#  * outgoing: give the queued item "Start-Position" as a CMTime, the key Apple's own senders
#    use (pyatv's "Start-Position-Seconds" is ignored on tvOS 27), and start at the browser's speed;
#  * incoming: forward the TV's playbackState and timeJumped events, which carry its position.
# Command and event shapes follow the TV's own events and LIVI's receiver (github.com/f-io/LIVI).
_ORIGINAL_SEND = airplayv2.AirPlayV2._send_url_command
_ORIGINAL_EVENT = airplayv2.AirPlayV2._handle_event
_LIVE: dict = {"rate": 1.0, "position": 0.0, "protocol": None, "on_event": None}

# play_url gives no start signal; still running this long without an error is the signal.
START_GRACE_S = 4.0
HANGUP_POLL_S = 1.0
# The TV keeps answering while a video sits paused, so without a limit the player, and the
# caffeinate holding off sleep, never end: a paused cast kept the Mac awake for 13 hours
# (2026-10-28). COUCHCAST_PAUSED_LIMIT_S shortens it for testing on a real TV.
PAUSED_LIMIT_S = float(os.environ.get("COUCHCAST_PAUSED_LIMIT_S") or 30 * 60)


def cmtime(seconds: float) -> dict:
    return {"value": int(round(seconds * 1000)), "timescale": 1000, "flags": 1, "epoch": 0}


def seconds_of(value) -> float | None:
    if isinstance(value, dict) and value.get("timescale"):
        return value.get("value", 0) / value["timescale"]
    if isinstance(value, (int, float)):
        return float(value)
    return None


async def _send_command(self, command: dict):
    _LIVE["protocol"] = self
    kind = command.get("type")
    if kind == "insertPlayQueueItem" and _LIVE["position"] > 0:
        command = {**command, "item": {**command["item"], "Start-Position": cmtime(_LIVE["position"])}}
        log.info("[airplay] queueing the video at %.0fs", _LIVE["position"])
    elif kind == "setRate" and command.get("rate") == 1.0 and _LIVE["rate"] != 1.0:
        command = {**command, "rate": _LIVE["rate"]}
        log.info("[airplay] starting playback at %sx", _LIVE["rate"])
    t0 = time.perf_counter()
    response = await _ORIGINAL_SEND(self, command)
    log.debug("[airplay] /command %s -> %s in %s", kind, getattr(response, "code", "?"), ms(t0))
    return response


def _handle_event(self, event: dict) -> None:
    _ORIGINAL_EVENT(self, event)
    listener = _LIVE["on_event"]
    if listener:
        try:
            listener(event)
        except Exception:
            log.exception("[airplay] event listener failed")


airplayv2.AirPlayV2._send_url_command = _send_command
airplayv2.AirPlayV2._handle_event = _handle_event


async def send_command(command: dict):
    protocol = _LIVE["protocol"]
    if protocol is None:
        raise RuntimeError("no AirPlay session to send to")
    return await protocol._send_url_command(command)


class EventTracker:
    """Turns the TV's events into position updates, and seeks once if the TV still starts short
    of the requested position."""

    def __init__(self, on_status: StatusCallback, target: float):
        self.on_status = on_status
        self.target = target
        self.seek_sent = target <= 0
        self.paused_since: float | None = None

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "playbackState":
            params = event.get("params") or {}
            name = str(params.get("playbackState") or event.get("name") or "").lower()
            position = seconds_of(params.get("position"))
            duration = seconds_of(params.get("duration"))
            log.info("[airplay] tv %s at=%s rate=%s", name, _fmt(position), params.get("rate"))
            if name == "paused" and self.paused_since is None:
                self.paused_since = time.monotonic()
            elif name == "playing":
                self.paused_since = None
            self.on_status(position, duration, name.capitalize() or None)
            if name == "playing" and position is not None:
                self._seek_if_short(position)
        elif kind == "notification":
            name = event.get("name")
            if name == "timeJumped":
                position = seconds_of(event.get("position"))
                log.info("[airplay] tv jumped to %s", _fmt(position))
                if self.paused_since is not None:
                    # Scrubbing a paused video means someone is still there.
                    self.paused_since = time.monotonic()
                self.on_status(position, None, None)
            else:
                log.debug("[airplay] tv notification %s", name)

    def _seek_if_short(self, position: float) -> None:
        if self.seek_sent or position >= self.target - 5:
            return
        self.seek_sent = True
        log.info("[airplay] tv started at %.0fs instead of %.0fs; seeking", position, self.target)
        asyncio.get_running_loop().create_task(self._seek())

    async def _seek(self) -> None:
        try:
            response = await send_command({"type": "seek", "time": cmtime(self.target)})
            log.info("[airplay] seek to %.0fs -> %s", self.target, getattr(response, "code", "?"))
        except Exception:
            log.exception("[airplay] seek failed")


def _fmt(seconds: float | None) -> str:
    return "?" if seconds is None else f"{seconds:.1f}s"


def ms(t0: float) -> str:
    return f"{(time.perf_counter() - t0) * 1000:.0f}ms"


async def open_storage(loop: asyncio.AbstractEventLoop) -> FileStorage:
    state.HOME.mkdir(parents=True, exist_ok=True)
    storage = FileStorage(str(state.PYATV_STORAGE), loop)
    await storage.load()
    state.lock_down(state.PYATV_STORAGE)
    return storage


async def scan_tvs(loop, storage, timeout: int = 5) -> list:
    t0 = time.perf_counter()
    confs = await pyatv.scan(loop, timeout=timeout, storage=storage)
    tvs = [c for c in confs if c.device_info.operating_system == OperatingSystem.TvOS]
    log.info("[airplay] scan found %d devices, %d Apple TVs in %s", len(confs), len(tvs), ms(t0))
    return tvs


def is_paired(conf) -> bool:
    service = conf.get_service(Protocol.AirPlay)
    return bool(service and service.credentials)


async def find_tv(loop, storage, device: dict):
    """Ask the last known address first (fast unicast), then fall back to a network-wide scan."""
    t0 = time.perf_counter()
    attempts = [[device["address"]], None] if device.get("address") else [None]
    for hosts in attempts:
        confs = await pyatv.scan(loop, identifier=device["identifier"], hosts=hosts, storage=storage, timeout=5)
        if confs:
            log.info("[airplay] found %s at %s (%s) in %s", confs[0].name, confs[0].address, "known address" if hosts else "scan", ms(t0))
            return confs[0]
    raise LookupError(f"{device['name']} isn't on the network. Is it plugged in and on the same Wi-Fi?")


async def begin_pairing(loop, storage, conf) -> PairingHandler:
    """Starts pairing; the TV shows a PIN once this returns."""
    t0 = time.perf_counter()
    pairing = await pyatv.pair(conf, Protocol.AirPlay, loop, storage=storage, name="Couchcast")
    try:
        await pairing.begin()
    except BaseException:
        await pairing.close()
        raise
    log.info("[airplay] pairing started with %s, device shows PIN=%s, in %s", conf.name, pairing.device_provides_pin, ms(t0))
    return pairing


async def finish_pairing(storage, pairing: PairingHandler, pin: int) -> None:
    t0 = time.perf_counter()
    try:
        pairing.pin(pin)
        await pairing.finish()
        if not pairing.has_paired:
            raise RuntimeError("The TV didn't accept the pairing. Try again and check the PIN.")
    finally:
        await pairing.close()
    await storage.save()
    state.lock_down(state.PYATV_STORAGE)
    log.info("[airplay] pairing finished in %s", ms(t0))


async def play(
    loop,
    storage,
    conf,
    url: str,
    position: float,
    on_started: Callable[[], None],
    on_status: StatusCallback | None = None,
    rate: float = 1.0,
) -> None:
    """Blocks until playback ends: AirPlay video stops if the sender hangs up."""
    t0 = time.perf_counter()
    _LIVE.update(rate=rate, position=position, protocol=None, on_event=EventTracker(on_status or (lambda *_: None), position))
    atv = await pyatv.connect(conf, loop, protocol=Protocol.AirPlay, storage=storage)
    log.info("[airplay] connected to %s in %s", conf.name, ms(t0))
    try:
        log.info("[airplay] play_url at=%.0fs rate=%sx", position, rate)
        task = asyncio.ensure_future(atv.stream.play_url(url, position=position))
        try:
            reason = await _supervise(task, on_started, conf.name, t0)
        except asyncio.CancelledError:
            task.cancel()
            raise
        if reason:
            log.info("[airplay] session on %s over after %s: %s", conf.name, ms(t0), reason)
        else:
            log.info("[airplay] playback ended on %s after %s", conf.name, ms(t0))
    finally:
        _LIVE.update(protocol=None, on_event=None)
        atv.close()


async def _supervise(task: asyncio.Future, on_started: Callable[[], None], tv_name: str, t0: float) -> str | None:
    """Waits for play_url, which returns only when the TV reports the video stopped. Returns why
    the session ended without that report instead: the TV hung up, or the video sat paused."""
    started = False
    supervising_since = time.perf_counter()
    while True:
        done, _ = await asyncio.wait({task}, timeout=HANGUP_POLL_S)
        if done:
            task.result()
            return None
        if not started and time.perf_counter() - supervising_since >= START_GRACE_S:
            started = True
            log.info("[airplay] playback running on %s (%s after connect)", tv_name, ms(t0))
            on_started()
        hangup = hung_up(_LIVE["protocol"])
        reason = hangup or paused_too_long(_LIVE["on_event"])
        if reason:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if hangup and not started:
                raise RuntimeError(f"The Apple TV hung up before the video started ({hangup}).")
            return reason


def paused_too_long(tracker) -> str | None:
    since = getattr(tracker, "paused_since", None)
    if since is None or time.monotonic() - since < PAUSED_LIMIT_S:
        return None
    return f"left paused for {(time.monotonic() - since) / 60:.0f} min"


def hung_up(protocol) -> str | None:
    """Backing out of the video on the TV closes the stream's connection and its event channel
    without a "stopped" event, and pyatv waits for that event forever. Only the remote-control
    heartbeat kept such a player alive, for 28 minutes once (2026-10-28)."""
    if protocol is None:
        return None
    if protocol.rtsp.connection.transport is None:
        return "the stream connection closed"
    if protocol.event_transport is not None and protocol.event_transport.is_closing():
        return "the event channel closed"
    return None
