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
