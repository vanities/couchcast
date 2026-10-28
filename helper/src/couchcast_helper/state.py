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


def setup_logging(verbose: bool = False, interactive: bool = True) -> None:
    """Always log to the file. Interactive commands keep the terminal for the UI and only echo
    logs there with --verbose; the Firefox host and the background player log to plain stderr,
    which Firefox shows in its browser console."""
    HOME.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=2)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
    handlers: list[logging.Handler] = [file_handler]
    if not interactive:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handlers.append(stderr_handler)
    elif verbose:
        from rich.logging import RichHandler

        from .ui import console

        handlers.append(RichHandler(console=console, show_path=False, markup=False, rich_tracebacks=True))
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not verbose:
        logging.getLogger("pyatv").setLevel(logging.WARNING)


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
