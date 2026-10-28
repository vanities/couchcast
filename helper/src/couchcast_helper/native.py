"""Firefox native messaging: each message is a 4-byte native-endian length, then UTF-8 JSON."""

import json
import logging
import struct
import sys
import time
from typing import BinaryIO

from . import __version__, player, state, stream

log = logging.getLogger("couchcast")


def read_message(source: BinaryIO) -> dict | None:
    header = source.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    return json.loads(source.read(length).decode("utf-8"))


def write_message(sink: BinaryIO, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sink.write(struct.pack("=I", len(data)))
    sink.write(data)
    sink.flush()


def handle(message: dict) -> dict:
    kind = message.get("type")
    config = state.read_json(state.CONFIG)
    device = config.get("device")
    if kind == "ping":
        return {"ok": True, "version": __version__, "device": device and device["name"]}
    if kind == "stop":
        return {"ok": True, "stopped": player.stop()}
    if kind != "cast":
        return {"ok": False, "error": f"Unknown request {kind!r}"}

    if not device:
        return {"ok": False, "error": "No Apple TV paired yet. Run: couchcast-helper pair"}
    t0 = time.perf_counter()
    try:
        resolved = stream.resolve(message["url"])
    except stream.StreamError as error:
        return {"ok": False, "error": str(error)}
    position = 0 if resolved.is_live else float(message.get("startSeconds") or 0)
    pid = player.spawn(resolved.url, position, resolved.title, device)
    outcome = player.wait_for_start(pid)
    log.info("[host] cast outcome=%s pid=%s in %.0fms", outcome.get("state"), pid, (time.perf_counter() - t0) * 1000)
    if outcome.get("state") == "error":
        return {"ok": False, "error": f"AirPlay failed: {outcome.get('error')}"}
    return {"ok": True, "tvName": device["name"], "title": resolved.title, "state": outcome.get("state")}


def serve() -> int:
    # Only protocol frames may reach stdout. Anything a library prints goes to stderr instead.
    sink = sys.stdout.buffer
    sys.stdout = sys.stderr
    message = read_message(sys.stdin.buffer)
    if message is None:
        log.warning("[host] stdin closed before a message arrived")
        return 1
    log.info("[host] request type=%s", message.get("type"))
    try:
        reply = handle(message)
    except Exception as error:
        log.exception("[host] request failed")
        reply = {"ok": False, "error": f"Couchcast helper error: {error}"}
    write_message(sink, reply)
    return 0
