import io
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from couchcast_helper import native, stream

HOST = Path(__file__).resolve().parents[1] / "bin" / "couchcast-host"


def frame(message: dict) -> bytes:
    data = json.dumps(message).encode()
    return struct.pack("=I", len(data)) + data


def test_native_messages_round_trip():
    sink = io.BytesIO()
    native.write_message(sink, {"type": "cast", "title": "Bunny 🐰"})
    assert native.read_message(io.BytesIO(sink.getvalue())) == {"type": "cast", "title": "Bunny 🐰"}


def test_read_message_returns_none_when_firefox_closes_the_pipe():
    assert native.read_message(io.BytesIO(b"")) is None


HLS = {"protocol": "m3u8_native", "manifest_url": "https://manifest.googlevideo.com/master.m3u8", "url": "https://x/variant.m3u8"}
MP4_360 = {"protocol": "https", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360, "url": "https://x/18.mp4"}
VIDEO_ONLY = {"protocol": "https", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "url": "https://x/137.mp4"}


@pytest.mark.parametrize(
    ("formats", "expected"),
    [
        ([VIDEO_ONLY, MP4_360, HLS], ("hls", "https://manifest.googlevideo.com/master.m3u8")),
        ([VIDEO_ONLY, MP4_360], ("progressive", "https://x/18.mp4")),
        ([VIDEO_ONLY], None),
    ],
)
def test_pick_stream_prefers_the_hls_master_then_a_muxed_mp4(formats, expected):
    picked = stream.pick_stream({"title": "t", "formats": formats})
    assert (picked and (picked.kind, picked.url)) == expected


def test_live_streams_are_flagged_so_no_start_position_is_sent():
    assert stream.pick_stream({"is_live": True, "formats": [HLS]}).is_live is True


def test_best_label_is_the_top_h264_rendition_since_the_apple_tv_skips_vp9():
    formats = [
        {**HLS, "vcodec": "avc1.4D401F", "height": 720, "fps": 60},
        {**HLS, "vcodec": "avc1.64002A", "height": 1080, "fps": 60},
        {**HLS, "vcodec": "vp09.00.51.08", "height": 2160, "fps": 60},
    ]
    assert stream.pick_stream({"formats": formats}).best == "1080p60"


def test_pyatv_pin_step_works_with_the_installed_srptools():
    """srptools 2.0 broke this exact call (SRPClientSession's `private` became keyword-only)."""
    from pyatv.auth.hap_srp import SRPAuthHandler

    handler = SRPAuthHandler()
    handler.initialize()
    handler.step1(7465)


def test_credential_files_are_locked_to_the_owner(tmp_path):
    from couchcast_helper import state

    creds = tmp_path / "pyatv.json"
    creds.write_text("{}")
    creds.chmod(0o644)
    state.lock_down(creds)
    assert creds.stat().st_mode & 0o777 == 0o600


def test_outgoing_commands_carry_the_start_position_and_speed(monkeypatch):
    import asyncio

    from couchcast_helper import airplay

    sent = []

    async def fake_send(_self, command):
        sent.append(command)

    monkeypatch.setattr(airplay, "_ORIGINAL_SEND", fake_send)
    monkeypatch.setattr(airplay, "_LIVE", {**airplay._LIVE, "rate": 1.5, "position": 257.0})
    item = {"uuid": "X", "Content-Location": "https://m/x.m3u8", "Start-Position-Seconds": 257.0}
    for command in ({"type": "insertPlayQueueItem", "item": item}, {"type": "setProperty"}, {"type": "setRate", "rate": 1.0}):
        asyncio.run(airplay._send_command(None, command))
    assert sent[0]["item"]["Start-Position"] == {"value": 257000, "timescale": 1000, "flags": 1, "epoch": 0}
    assert sent[0]["item"]["Content-Location"] == "https://m/x.m3u8"
    assert sent[1] == {"type": "setProperty"}
    assert sent[2] == {"type": "setRate", "rate": 1.5}


def test_cmtime_round_trips():
    from couchcast_helper import airplay

    assert airplay.seconds_of(airplay.cmtime(257.25)) == 257.25
    assert airplay.seconds_of({"value": 2351049, "timescale": 1000, "flags": 1, "epoch": 0}) == 2351.049
    assert airplay.seconds_of(None) is None


def test_tv_events_become_positions_and_a_short_start_triggers_one_seek(monkeypatch):
    """Event shapes copied from a real tvOS 27 session (helper.log, 2026-10-28)."""
    import asyncio

    from couchcast_helper import airplay

    statuses, seeks = [], []

    async def fake_send(command):
        seeks.append(command)

    monkeypatch.setattr(airplay, "send_command", fake_send)
    tracker = airplay.EventTracker(lambda *status: statuses.append(status), target=257)
    zero = {"flags": 1, "value": 0, "timescale": 1, "epoch": 0}

    async def feed():
        tracker({"type": "playbackState", "name": "loading"})
        tracker({"type": "playbackState", "params": {"playbackState": "playing", "position": zero, "rate": 1.0,
                                                     "duration": {"value": 2351049, "timescale": 1000}}})
        tracker({"type": "playbackState", "params": {"playbackState": "playing", "position": zero}})
        tracker({"type": "notification", "name": "timeJumped", "position": {"value": 257000, "timescale": 1000}})
        await asyncio.sleep(0)

    asyncio.run(feed())
    assert statuses == [(None, None, "Loading"), (0.0, 2351.049, "Playing"), (0.0, None, "Playing"), (257.0, None, None)]
    assert seeks == [{"type": "seek", "time": {"value": 257000, "timescale": 1000, "flags": 1, "epoch": 0}}]


@pytest.fixture
def couchcast_home(tmp_path, monkeypatch):
    from couchcast_helper import state

    monkeypatch.setattr(state, "HOME", tmp_path)
    monkeypatch.setattr(state, "PLAYER", tmp_path / "player.json")
    monkeypatch.setattr(state, "CONFIG", tmp_path / "config.json")
    return state


def test_live_position_runs_on_at_the_cast_speed(couchcast_home):
    import os
    import time

    from couchcast_helper import player

    couchcast_home.write_json(
        couchcast_home.PLAYER,
        {"pid": os.getpid(), "state": "playing", "position": 100.0, "positionAt": time.time() - 10, "rate": 1.5},
    )
    assert player.current()["livePosition"] == pytest.approx(115, abs=0.5)


def test_a_finishing_session_does_not_overwrite_the_cast_that_replaced_it(couchcast_home):
    import os

    from couchcast_helper import player

    session = player.Session("old", {"name": "Bedroom"}, 0)
    newer = {"pid": os.getpid() + 1, "state": "playing", "title": "new"}
    couchcast_home.write_json(couchcast_home.PLAYER, newer)
    session.mark("stopped")
    assert couchcast_home.read_json(couchcast_home.PLAYER)["title"] == "new"


def test_status_reports_the_tv_position_for_tab_catch_up(couchcast_home):
    import os
    import time

    couchcast_home.write_json(
        couchcast_home.PLAYER,
        {"pid": os.getpid(), "state": "paused", "videoId": "aqz-KE-bpKQ", "position": 42.0, "positionAt": time.time(), "device": "Bedroom"},
    )
    reply = native.handle({"type": "status"})
    assert reply == {
        "ok": True,
        "active": True,
        "state": "paused",
        "videoId": "aqz-KE-bpKQ",
        "position": 42.0,
        "duration": None,
        "rate": None,
        "tvName": "Bedroom",
    }


def test_cli_lists_commands_and_hides_the_firefox_ones():
    from typer.testing import CliRunner

    from couchcast_helper.__main__ import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("scan", "pair", "play", "stop", "install", "status"):
        assert command in result.output
    assert "host" not in result.output and "player" not in result.output


def test_host_wrapper_answers_ping_under_firefoxs_bare_environment(tmp_path):
    """Firefox launches native apps with a minimal env; the wrapper has to find uv by itself."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "COUCHCAST_HOME": str(tmp_path)}
    proc = subprocess.run([str(HOST), "/fake/manifest.json", "couchcast@vanities"], input=frame({"type": "ping"}), capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    (length,) = struct.unpack("=I", proc.stdout[:4])
    reply = json.loads(proc.stdout[4 : 4 + length])
    assert reply["ok"] is True and reply["device"] is None
    assert len(proc.stdout) == 4 + length, "nothing but the reply may reach stdout"
