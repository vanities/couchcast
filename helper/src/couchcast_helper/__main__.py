import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import EXTENSION_ID, HOST_NAME, airplay, native, player, state, stream

NATIVE_HOST_DIRS = {
    "darwin": Path.home() / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts",
    "linux": Path.home() / ".mozilla" / "native-messaging-hosts",
}


def cmd_scan(_args) -> int:
    async def run():
        loop = asyncio.get_running_loop()
        storage = await airplay.open_storage(loop)
        for conf in await airplay.scan_tvs(loop, storage):
            service = conf.get_service(airplay.Protocol.AirPlay)
            paired = "paired" if service and service.credentials else "not paired"
            print(f"{conf.name:<20} {conf.address!s:<16} {conf.identifier}  airplay: {paired}")

    asyncio.run(run())
    return 0


def cmd_pair(args) -> int:
    async def run():
        loop = asyncio.get_running_loop()
        storage = await airplay.open_storage(loop)
        tvs = await airplay.scan_tvs(loop, storage)
        if args.device:
            tvs = [t for t in tvs if args.device.lower() in (t.name.lower(), str(t.identifier).lower())]
        if not tvs:
            raise SystemExit("No Apple TV found on this network.")
        if len(tvs) > 1:
            raise SystemExit("Several Apple TVs found; pick one with --device: " + ", ".join(t.name for t in tvs))
        tv = tvs[0]
        print(f"Pairing with {tv.name}. A PIN should appear on the TV.")

        async def read_pin() -> int:
            return int((await loop.run_in_executor(None, input, "PIN shown on the TV: ")).strip())

        await airplay.pair(loop, storage, tv, read_pin)
        config = state.read_json(state.CONFIG)
        config["device"] = {"name": tv.name, "identifier": tv.identifier, "address": str(tv.address)}
        state.write_json(state.CONFIG, config)
        print(f"Paired. Couchcast will play on {tv.name}.")

    asyncio.run(run())
    return 0


def cmd_play(args) -> int:
    device = state.read_json(state.CONFIG).get("device")
    if not device:
        raise SystemExit("Pair first: couchcast-helper pair")
    resolved = stream.resolve(args.url)
    print(f"{resolved.kind} stream for {resolved.title!r}; sending to {device['name']}")
    if args.background:
        outcome = player.wait_for_start(player.spawn(resolved.url, args.at, resolved.title, device))
        print(json.dumps(outcome))
        return 0 if outcome.get("state") != "error" else 1
    return player.run(resolved.url, args.at, resolved.title or "", device)


def cmd_player(args) -> int:
    device = state.read_json(state.CONFIG)["device"]
    return player.run(args.url, args.position, args.title, device)


def cmd_install(_args) -> int:
    directory = NATIVE_HOST_DIRS.get(sys.platform)
    if directory is None:
        raise SystemExit(f"Don't know where Firefox looks for native apps on {sys.platform}")
    wrapper = Path(__file__).resolve().parents[2] / "bin" / "couchcast-host"
    manifest = {
        "name": HOST_NAME,
        "description": "Couchcast helper: plays YouTube on an Apple TV over AirPlay",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{HOST_NAME}.json"
    target.write_text(json.dumps(manifest, indent=2))
    print(f"Registered {target} -> {wrapper}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="couchcast-helper")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="list Apple TVs on the network").set_defaults(func=cmd_scan)
    pair = sub.add_parser("pair", help="pair with an Apple TV (PIN shown on the TV)")
    pair.add_argument("--device", help="name or identifier, when there are several")
    pair.set_defaults(func=cmd_pair)
    play = sub.add_parser("play", help="play a YouTube URL on the paired Apple TV")
    play.add_argument("url")
    play.add_argument("--at", type=float, default=0, help="start position in seconds")
    play.add_argument("--background", action="store_true", help="hand off to a background player like the extension does")
    play.set_defaults(func=cmd_play)
    sub.add_parser("stop", help="stop the current cast").set_defaults(func=lambda _a: print(player.stop()) or 0)
    sub.add_parser("install", help="register with Firefox as a native app").set_defaults(func=cmd_install)
    host = sub.add_parser("host", help=argparse.SUPPRESS)
    # Firefox appends the manifest path and the calling extension's id.
    host.add_argument("firefox_args", nargs="*")
    host.set_defaults(func=lambda _a: native.serve())
    internal = sub.add_parser("player", help=argparse.SUPPRESS)
    internal.add_argument("--url", required=True)
    internal.add_argument("--position", type=float, default=0)
    internal.add_argument("--title", default="")
    internal.set_defaults(func=cmd_player)

    args = parser.parse_args()
    state.setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
