"""Keeps the parts of Couchcast's addons.mozilla.org listing that `web-ext sign` doesn't send: the
privacy policy, the icon and the screenshots. Uses the API key in .dev/amo.env.

    uv run --with requests python scripts/amo_listing.py status
    uv run --with requests python scripts/amo_listing.py privacy       # amo/privacy-policy.md
    uv run --with requests python scripts/amo_listing.py icon          # amo/icon-128.png
    uv run --with requests python scripts/amo_listing.py screenshots   # amo/screenshots/, replacing the old ones
    uv run --with requests python scripts/amo_listing.py captions      # only the missing captions
"""

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://addons.mozilla.org/api/v5/addons/addon/couchcast/"
CAPTIONS = {
    "settings-light.png": "Pick how it plays: no ads, straight to an Apple TV, or the YouTube app on any TV.",
    "settings-dark.png": "The settings page follows Firefox's dark mode.",
}


def say(message: str) -> None:
    print(f"[amo {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def credentials() -> tuple[str, str]:
    values = {}
    for line in (ROOT / ".dev" / "amo.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    return values["WEB_EXT_API_KEY"], values["WEB_EXT_API_SECRET"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def auth_header() -> dict:
    """AMO wants a fresh HS256 JWT per request, signed with the key's secret, valid at most 5 minutes."""
    issuer, secret = credentials()
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"iss": issuer, "jti": secrets.token_hex(16), "iat": now, "exp": now + 60}).encode())
    signature = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return {"Authorization": f"JWT {header}.{payload}.{signature}"}


def call(method: str, path: str = "", **kwargs) -> dict:
    for attempt in range(1, 6):
        for value in kwargs.get("files", {}).values():
            value[1].seek(0)
        t0 = time.perf_counter()
        response = requests.request(method, API + path, headers=auth_header(), timeout=120, **kwargs)
        say(f"{method} {path or '/'} -> {response.status_code} in {(time.perf_counter() - t0) * 1000:.0f}ms")
        if response.status_code != 429:
            break
        # AMO throttles listing writes: seconds after a burst, and about 20 minutes after several
        # screenshot uploads. Wait out the short ones; give up on the long ones.
        wait = int(response.headers.get("Retry-After") or 15) + 1
        if wait > 120:
            raise SystemExit(f"[amo] throttled for {wait}s; run this again after {time.strftime('%H:%M', time.localtime(time.time() + wait))}")
        say(f"throttled; retrying in {wait}s (attempt {attempt}/5)")
        time.sleep(wait)
    if not response.ok:
        say(f"response: {response.text[:800]}")
        response.raise_for_status()
    return response.json() if response.content else {}


def status() -> None:
    addon = call("GET")
    current = addon.get("current_version") or {}
    say(
        f"slug={addon['slug']} status={addon['status']} experimental={addon['is_experimental']} "
        f"public_page={addon['url']} current_version={current.get('version')} "
        f"icon={'custom' if addon.get('icon_url') and 'default' not in addon['icon_url'] else 'default'} "
        f"previews={len(addon.get('previews') or [])} categories={addon.get('categories')} "
        f"homepage={(addon.get('homepage') or {}).get('url')}"
    )
    versions = call("GET", "versions/?filter=all_with_unlisted")
    for version in versions.get("results", []):
        say(f"version {version['version']} channel={version['channel']} file={version['file']['status']}")


def privacy() -> None:
    text = (ROOT / "amo" / "privacy-policy.md").read_text()
    call("PATCH", "eula_policy/", json={"privacy_policy": {"en-US": text}})
    say(f"privacy policy set ({len(text)} chars)")


def icon() -> None:
    path = ROOT / "amo" / "icon-128.png"
    with path.open("rb") as image:
        call("PATCH", files={"icon": (path.name, image, "image/png")})
    say(f"icon set from {path.name}")


def screenshots() -> None:
    for preview in call("GET").get("previews") or []:
        call("DELETE", f"previews/{preview['id']}/")
    for position, name in enumerate(CAPTIONS):
        path = ROOT / "amo" / "screenshots" / name
        with path.open("rb") as image:
            created = call("POST", "previews/", files={"image": (name, image, "image/png")}, data={"position": position})
        # AMO won't take a caption in the same request as the image.
        call("PATCH", f"previews/{created['id']}/", json={"caption": {"en-US": CAPTIONS[name]}})
        say(f"screenshot {position + 1}: {name}")


def captions() -> None:
    """Captions for screenshots that don't have one yet, in upload order."""
    previews = sorted(call("GET").get("previews") or [], key=lambda preview: preview["position"])
    for preview, caption in zip(previews, CAPTIONS.values()):
        if (preview.get("caption") or {}).get("en-US") != caption:
            call("PATCH", f"previews/{preview['id']}/", json={"caption": {"en-US": caption}})
            say(f"captioned preview {preview['id']}")


COMMANDS = {"status": status, "privacy": privacy, "icon": icon, "screenshots": screenshots, "captions": captions}

if __name__ == "__main__":
    for command in sys.argv[1:] or ["status"]:
        COMMANDS[command]()
