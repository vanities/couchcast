import logging
import time
from dataclasses import dataclass

log = logging.getLogger("couchcast")


class StreamError(Exception):
    pass


@dataclass
class Stream:
    url: str
    kind: str  # "hls" or "progressive"
    title: str | None
    is_live: bool
    duration: float | None = None
    best: str | None = None  # e.g. "1080p60", the best H.264 rendition the Apple TV can pick


def _label(fmt: dict) -> str | None:
    height, fps = fmt.get("height"), fmt.get("fps")
    if not height:
        return None
    return f"{height}p{int(fps)}" if fps and fps > 30 else f"{height}p"


def pick_stream(info: dict) -> Stream | None:
    """Prefer YouTube's HLS master playlist: the Apple TV's player adapts it up to 1080p60, and
    unlike the direct-file URLs its IP lock isn't enforced, so the TV can fetch what the Mac
    resolved. A muxed MP4 (360p) is the fallback."""
    formats = info.get("formats") or []
    common = {"title": info.get("title"), "is_live": bool(info.get("is_live")), "duration": info.get("duration")}
    hls = [f for f in formats if str(f.get("protocol", "")).startswith("m3u8") and f.get("manifest_url")]
    if hls:
        h264 = [f for f in hls if str(f.get("vcodec", "")).startswith("avc1")]
        top = max(h264, key=lambda f: (f.get("height") or 0, f.get("fps") or 0), default=None)
        return Stream(hls[0]["manifest_url"], "hls", best=top and _label(top), **common)
    muxed = [
        f
        for f in formats
        if f.get("ext") == "mp4"
        and f.get("vcodec", "none") != "none"
        and f.get("acodec", "none") != "none"
        and str(f.get("protocol", "")).startswith("http")
    ]
    if muxed:
        top = max(muxed, key=lambda f: f.get("height") or 0)
        return Stream(top["url"], "progressive", best=_label(top), **common)
    return None


def resolve(page_url: str) -> Stream:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    t0 = time.perf_counter()
    log.info("[stream] resolve start url=%s", page_url)
    options = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except DownloadError as error:
        log.error("[stream] yt-dlp failed url=%s in %.0fms: %s", page_url, (time.perf_counter() - t0) * 1000, error)
        raise StreamError(f"YouTube wouldn't give up this video's stream: {error}") from error
    stream = pick_stream(info or {})
    if stream is None:
        raise StreamError("No stream the Apple TV can play was found for this video.")
    log.info(
        "[stream] resolve done kind=%s live=%s title=%r in %.0fms",
        stream.kind,
        stream.is_live,
        stream.title,
        (time.perf_counter() - t0) * 1000,
    )
    return stream
