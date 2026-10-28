import asyncio
import logging
import time
from collections.abc import Callable

import pyatv
from pyatv.const import FeatureName, FeatureState, OperatingSystem, Protocol
from pyatv.interface import PairingHandler, PushListener
from pyatv.protocols.raop.protocols import airplayv2
from pyatv.storage.file_storage import FileStorage

from . import state

log = logging.getLogger("couchcast")

# (position, total_time, device_state) from the TV, any of which can be None.
StatusCallback = Callable[[float | None, float | None, str | None], None]


# pyatv's play_url always starts AirPlay 2 video at rate 1.0 and keeps its /command channel
# private. Wrapping the one method that sends those commands lets playback start at the
# browser's speed. Tied to the pinned pyatv commit; drop it once play_url takes a rate.
_ORIGINAL_SEND = airplayv2.AirPlayV2._send_url_command
_START_RATE = {"value": 1.0}


async def _send_at_start_rate(self, command):
    rate = _START_RATE["value"]
    if command.get("type") == "setRate" and command.get("rate") == 1.0 and rate != 1.0:
        log.info("[airplay] starting playback at %sx", rate)
        command = {**command, "rate": rate}
    log.debug("[airplay] /command %s", command.get("type"))
    return await _ORIGINAL_SEND(self, command)


airplayv2.AirPlayV2._send_url_command = _send_at_start_rate


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


class _StatusListener(PushListener):
    def __init__(self, callback: StatusCallback) -> None:
        self.callback = callback

    def playstatus_update(self, updater, playstatus) -> None:
        state_name = playstatus.device_state.name if playstatus.device_state else None
        log.debug("[airplay] status %s at=%s/%s", state_name, playstatus.position, playstatus.total_time)
        self.callback(playstatus.position, playstatus.total_time, state_name)

    def playstatus_error(self, updater, exception: Exception) -> None:
        log.debug("[airplay] status update error: %s", exception)


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
    _START_RATE["value"] = rate
    atv = await pyatv.connect(conf, loop, protocol=Protocol.AirPlay, storage=storage)
    log.info("[airplay] connected to %s in %s", conf.name, ms(t0))
    try:
        if on_status:
            if atv.features.in_state(FeatureState.Available, FeatureName.PushUpdates):
                atv.push_updater.listener = _StatusListener(on_status)
                atv.push_updater.start()
                log.info("[airplay] listening for playback status from %s", conf.name)
            else:
                log.info("[airplay] %s doesn't push playback status over this connection", conf.name)
        log.info("[airplay] play_url at=%.0fs rate=%sx", position, rate)
        task = asyncio.ensure_future(atv.stream.play_url(url, position=position))
        # play_url never returns while the video plays, so "still running after a few seconds
        # without an error" is the only start signal it gives.
        done, _ = await asyncio.wait({task}, timeout=4)
        if not done:
            log.info("[airplay] playback running on %s at=%.0fs (%s after connect)", conf.name, position, ms(t0))
            on_started()
        await task
        log.info("[airplay] playback ended on %s after %s", conf.name, ms(t0))
    finally:
        atv.close()
