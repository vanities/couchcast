import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import pyatv
from pyatv.const import OperatingSystem, Protocol
from pyatv.storage.file_storage import FileStorage

from . import state

log = logging.getLogger("couchcast")


def ms(t0: float) -> str:
    return f"{(time.perf_counter() - t0) * 1000:.0f}ms"


async def open_storage(loop: asyncio.AbstractEventLoop) -> FileStorage:
    state.HOME.mkdir(parents=True, exist_ok=True)
    storage = FileStorage(str(state.PYATV_STORAGE), loop)
    await storage.load()
    return storage


async def scan_tvs(loop, storage, timeout: int = 5) -> list:
    t0 = time.perf_counter()
    confs = await pyatv.scan(loop, timeout=timeout, storage=storage)
    tvs = [c for c in confs if c.device_info.operating_system == OperatingSystem.TvOS]
    log.info("[airplay] scan found %d devices, %d Apple TVs in %s", len(confs), len(tvs), ms(t0))
    return tvs


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


async def pair(loop, storage, conf, read_pin: Callable[[], Awaitable[int]]) -> None:
    t0 = time.perf_counter()
    pairing = await pyatv.pair(conf, Protocol.AirPlay, loop, storage=storage, name="Couchcast")
    try:
        await pairing.begin()
        if pairing.device_provides_pin:
            pairing.pin(await read_pin())
        else:
            pairing.pin(1111)
            print("Enter 1111 on the TV.")
        await pairing.finish()
        if not pairing.has_paired:
            raise RuntimeError("Pairing did not complete. Try again and check the PIN.")
    finally:
        await pairing.close()
    await storage.save()
    log.info("[airplay] paired with %s in %s", conf.name, ms(t0))


async def play(loop, storage, conf, url: str, position: float, on_started: Callable[[], None]) -> None:
    """Blocks until playback ends: AirPlay video stops if the sender hangs up."""
    t0 = time.perf_counter()
    atv = await pyatv.connect(conf, loop, protocol=Protocol.AirPlay, storage=storage)
    log.info("[airplay] connected to %s in %s", conf.name, ms(t0))
    try:
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
