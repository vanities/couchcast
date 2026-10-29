"""Firefox native messaging: each message is a 4-byte native-endian length, then UTF-8 JSON."""

import json
import logging
import math
import re
import struct
import sys
import time
from typing import BinaryIO

from . import __version__, player, state, stream

log = logging.getLogger("couchcast")

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


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


def tv_status() -> dict:
    info = player.current()
    active = bool(info.get("alive")) and info.get("state") in player.ACTIVE
    return {
        "ok": True,
        "active": active,
        "state": info.get("state"),
        "videoId": info.get("videoId"),
        "position": info.get("livePosition"),
        "duration": info.get("duration"),
        "rate": info.get("rate"),
        "tvName": info.get("device"),
    }


def handle(message: dict) -> dict:
    kind = message.get("type")
    device = state.read_json(state.CONFIG).get("device")
    if kind == "ping":
        return {"ok": True, "version": __version__, "device": device and device["name"]}
    if kind == "status":
        return tv_status()
    if kind == "stop":
        return {"ok": True, "stopped": player.stop()}
    if kind != "cast":
        return {"ok": False, "error": f"Unknown request {kind!r}"}

    if not device:
        return {"ok": False, "error": "No Apple TV paired yet. Run: couchcast-helper pair"}
    video_id = message.get("videoId")
    # yt-dlp reads 1,000+ sites and any URL, so the helper builds the YouTube URL itself.
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        log.warning("[host] cast refused: videoId=%.40r is not a YouTube video id", video_id)
        return {"ok": False, "error": "That isn't a YouTube video."}
    try:
        resolved = stream.resolve(f"https://www.youtube.com/watch?v={video_id}", browser_login=message.get("useBrowserLogin") is True)
    except stream.StreamError as error:
        return {"ok": False, "error": str(error)}
    position = 0.0 if resolved.is_live else _bounded(message.get("startSeconds"), 0.0, 0.0, 7 * 24 * 3600)
    rate = _bounded(message.get("rate"), 1.0, 0.25, 4.0)
    pid = player.spawn(resolved.url, position, resolved.title, device, video_id=video_id, rate=rate)
    outcome = player.wait_for_start(pid)
    if outcome.get("state") == "error":
        return {"ok": False, "error": f"AirPlay failed: {outcome.get('error')}"}
    return {"ok": True, "tvName": device["name"], "title": resolved.title, "state": outcome.get("state")}


def _bounded(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isfinite(number) and low <= number <= high:
        return number
    log.warning("[host] %r is outside %s..%s; using %s", value, low, high, default)
    return default


def serve() -> int:
    # Only protocol frames may reach stdout. Anything a library prints goes to stderr instead.
    sink = sys.stdout.buffer
    sys.stdout = sys.stderr
    t0 = time.perf_counter()
    message = read_message(sys.stdin.buffer)
    if message is None:
        log.warning("[host] stdin closed before a message arrived")
        return 1
    details = {k: message[k] for k in ("videoId", "startSeconds", "rate", "useBrowserLogin") if k in message}
    log.info("[host] request type=%s %s", message.get("type"), details or "")
    try:
        reply = handle(message)
    except Exception as error:
        log.exception("[host] request failed")
        reply = {"ok": False, "error": f"Couchcast helper error: {error}"}
    write_message(sink, reply)
    # The whole process, Python's startup included: Firefox starts one for every request.
    cpu, peak_mb = state.usage()
    log.info(
        "[host] reply type=%s ok=%s %s in %.0fms (process cpu %.0fms, peak %.0fMB)",
        message.get("type"),
        reply.get("ok"),
        reply.get("error") or reply.get("state") or "",
        (time.perf_counter() - t0) * 1000,
        cpu * 1000,
        peak_mb,
    )
    return 0
