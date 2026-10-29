import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

HOME = Path(os.environ.get("COUCHCAST_HOME", Path.home() / ".config" / "couchcast"))
CONFIG = HOME / "config.json"
PYATV_STORAGE = HOME / "pyatv.json"
PLAYER = HOME / "player.json"
LOG = HOME / "helper.log"

log = logging.getLogger("couchcast")


def debug_enabled() -> bool:
    """COUCHCAST_DEBUG=1, or "debug": true in config.json. The config switch exists because
    Firefox starts the helper with its own environment, where an env var can't be set."""
    return os.environ.get("COUCHCAST_DEBUG") == "1" or bool(read_json(CONFIG).get("debug"))


class PrivateRotatingFileHandler(RotatingFileHandler):
    """The log names videos and holds stream URLs. RotatingFileHandler opens each fresh file with
    the default umask (world-readable), so lock every file it opens."""

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def setup_logging(verbose: bool = False, interactive: bool = True, echo_stderr: bool = False) -> None:
    """Always log to the file. Interactive commands keep the terminal for the UI and only echo
    logs there with --verbose. The Firefox host also echoes to stderr, which Firefox shows in
    its browser console; a background player's stderr already goes to the log file."""
    HOME.mkdir(parents=True, exist_ok=True)
    debug = debug_enabled()
    file_handler = PrivateRotatingFileHandler(LOG, maxBytes=20_000_000, backupCount=2)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
    for backup in (LOG.with_name(f"{LOG.name}.1"), LOG.with_name(f"{LOG.name}.2")):
        lock_down(backup)
    handlers: list[logging.Handler] = [file_handler]
    if echo_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handlers.append(stderr_handler)
    elif verbose:
        from rich.logging import RichHandler

        from .ui import console

        handlers.append(RichHandler(console=console, show_path=False, markup=False, rich_tracebacks=True))
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.DEBUG if verbose or debug else logging.INFO)
    if not (verbose or debug):
        logging.getLogger("pyatv").setLevel(logging.WARNING)
    # mDNS chatter drowns everything else even in debug mode.
    logging.getLogger("zeroconf").setLevel(logging.INFO)
    log.debug("[state] logging ready verbose=%s debug=%s interactive=%s argv=%s", verbose, debug, interactive, sys.argv[1:3])


def lock_down(path: Path) -> None:
    """Owner-only. pyatv writes its credential file with the default umask (world-readable)."""
    if path.exists() and path.stat().st_mode & 0o077:
        path.chmod(0o600)
        log.debug("[state] restricted %s to owner-only", path.name)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        log.warning("[state] %s is not valid JSON (%s); ignoring it", path.name, error)
        return {}


def write_json(path: Path, data: dict) -> None:
    """Atomic, owner-only: these files hold AirPlay credentials and device ids."""
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
