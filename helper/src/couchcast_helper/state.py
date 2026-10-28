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


def setup_logging(verbose: bool = False) -> None:
    """File log always; stderr too (Firefox shows a native host's stderr in the browser console)."""
    HOME.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=2)
    stderr_handler = logging.StreamHandler(sys.stderr)
    for handler in (file_handler, stderr_handler):
        handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [file_handler, stderr_handler]
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
