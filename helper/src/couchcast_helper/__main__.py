import asyncio
import functools
import json
import logging
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import box
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from . import EXTENSION_ID, HOST_NAME, __version__, airplay, native, player, state, stream, ui

log = logging.getLogger("couchcast")

NATIVE_HOST_DIRS = {
    "darwin": Path.home() / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts",
    "linux": Path.home() / ".mozilla" / "native-messaging-hosts",
}
# Commands Firefox and the host start without a terminal: no UI, plain logs on stderr.
HEADLESS = {"host", "player"}

app = typer.Typer(
    name="couchcast-helper",
    help="Plays YouTube on your Apple TV over AirPlay, with no ads. The helper app for the Couchcast Firefox extension.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)


def _print_version(value: bool) -> None:
    if value:
        ui.console.print(f"couchcast-helper {__version__}")
        raise typer.Exit()


@app.callback()
def _setup(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed logs as it works.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_print_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    state.setup_logging(
        verbose=verbose, interactive=ctx.invoked_subcommand not in HEADLESS, echo_stderr=ctx.invoked_subcommand == "host"
    )


def guarded(func):
    """Ctrl-C becomes a clean stop and other failures a one-line message; the traceback goes to
    the log either way."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        log.info("[cli] %s start %s", func.__name__, kwargs or "")
        try:
            result = func(*args, **kwargs)
            log.info("[cli] %s done in %.0fms", func.__name__, (time.perf_counter() - t0) * 1000)
            return result
        except typer.Exit as done:
            log.info("[cli] %s exit=%s in %.0fms", func.__name__, done.exit_code, (time.perf_counter() - t0) * 1000)
            raise
        except typer.Abort:
            raise
        except KeyboardInterrupt:
            log.warning("[cli] %s interrupted", func.__name__)
            ui.console.print("\n[muted]■ Stopped.[/]")
            raise typer.Exit(130) from None
        except Exception as error:
            log.exception("[cli] %s failed", func.__name__)
            ui.error(friendly(error), hint=f"Details are in {state.LOG}")
            raise typer.Exit(1) from None

    return wrapper


def firefox_manifest() -> Path | None:
    directory = NATIVE_HOST_DIRS.get(sys.platform)
    return directory / f"{HOST_NAME}.json" if directory else None


def describe_model(conf) -> str:
    info = conf.device_info
    return f"{info.model_str}, tvOS {info.version}" if info.version else info.model_str


def require_device() -> dict:
    device = state.read_json(state.CONFIG).get("device")
    if not device:
        ui.error("No Apple TV paired yet.", hint="Run: couchcast-helper pair")
        raise typer.Exit(1)
    return device


@app.command()
@guarded
def scan() -> None:
    """List the Apple TVs on your network."""

    async def run():
        loop = asyncio.get_running_loop()
        return await airplay.scan_tvs(loop, await airplay.open_storage(loop))

    with ui.step("Looking for Apple TVs on your network…"):
        tvs = asyncio.run(run())
    if not tvs:
        ui.warn("No Apple TVs found.", hint="Is this computer on the same network as the TV?")
        raise typer.Exit(1)
    default = (state.read_json(state.CONFIG).get("device") or {}).get("identifier")
    table = Table(box=box.SIMPLE_HEAD, header_style="accent", pad_edge=False)
    for column in ("Name", "Model", "Address", "AirPlay"):
        table.add_column(column)
    for tv in tvs:
        status = "[ok]paired[/]" if airplay.is_paired(tv) else "[muted]not paired[/]"
        if tv.identifier == default:
            status += " [accent]· default[/]"
        table.add_row(f"[key]{tv.name}[/]", describe_model(tv), str(tv.address), status)
    ui.console.print(table)


@app.command()
@guarded
def pair(
    device: Annotated[Optional[str], typer.Option("--device", "-d", help="Apple TV name, if you have several.")] = None,
) -> None:
    """Pair with an Apple TV. It shows a PIN that you type here."""
    asyncio.run(_pair(device))


async def _pair(name: str | None) -> None:
    loop = asyncio.get_running_loop()
    storage = await airplay.open_storage(loop)
    with ui.step("Looking for Apple TVs on your network…"):
        tvs = await airplay.scan_tvs(loop, storage)
    if name:
        tvs = [t for t in tvs if name.lower() in (t.name.lower(), str(t.identifier).lower())]
    if not tvs:
        ui.error(f"No Apple TV{f' named {name!r}' if name else ''} found.", hint="Is this computer on the same network as the TV?")
        raise typer.Exit(1)
    tv = tvs[0] if len(tvs) == 1 else tvs[ui.ask_choice("Which Apple TV?", [f"{t.name} [muted]{describe_model(t)}[/]" for t in tvs])]

    ui.console.print(f"Pairing with [key]{tv.name}[/] [muted]{describe_model(tv)} · {tv.address}[/]")
    with ui.step(f"Asking {tv.name} for a PIN…"):
        pairing = await airplay.begin_pairing(loop, storage, tv)
    try:
        if pairing.device_provides_pin:
            # Prompt on the loop's thread on purpose: a prompt in a worker thread keeps the
            # process alive after Ctrl-C until someone presses Enter.
            pin = ui.ask_pin()
        else:
            pin = 1111
            ui.console.print("Type [key]1111[/] on the TV.")
    except BaseException:
        await pairing.close()
        raise
    with ui.step(f"Checking the PIN with {tv.name}…"):
        await airplay.finish_pairing(storage, pairing, pin)

    config = state.read_json(state.CONFIG)
    config["device"] = {"name": tv.name, "identifier": tv.identifier, "address": str(tv.address)}
    state.write_json(state.CONFIG, config)

    target = firefox_manifest()
    steps = []
    if not (target and target.exists()):
        steps.append("[key]couchcast-helper install[/]   [muted]connect it to Firefox[/]")
    steps.append("[key]couchcast-helper play[/] [muted]<youtube url>   try it from here[/]")
    ui.console.print(
        Panel.fit(
            f"[ok]✓ Paired with {tv.name}[/]\n\nNext:\n  " + "\n  ".join(steps),
            border_style="green",
            title="[accent]couchcast[/]",
            title_align="left",
        )
    )


@app.command()
@guarded
def play(
    url: Annotated[str, typer.Argument(help="A YouTube video URL.")],
    at: Annotated[float, typer.Option("--at", help="Start position, in seconds.")] = 0,
    rate: Annotated[float, typer.Option("--rate", help="Playback speed, like 1.5.")] = 1.0,
    background: Annotated[
        bool, typer.Option("--background", help="Hand off to a background player, the way the extension does.")
    ] = False,
) -> None:
    """Play a YouTube video on your Apple TV, with no ads."""
    device = require_device()
    with ui.step("Finding the video stream…"):
        resolved = stream.resolve(url)
    details = [
        "live" if resolved.is_live else None,
        f"up to {resolved.best}" if resolved.best else None,
        ui.clock(resolved.duration) if resolved.duration and not resolved.is_live else None,
    ]
    details = " · ".join(d for d in details if d)
    ui.console.print(f"[accent]▶[/] [key]{resolved.title or url}[/]" + (f"  [muted]{details}[/]" if details else ""))
    position = 0 if resolved.is_live else at

    if background:
        with ui.step(f"Starting playback on {device['name']}…"):
            outcome = player.wait_for_start(player.spawn(resolved.url, position, resolved.title, device, rate=rate))
        if outcome.get("state") == "error":
            ui.error(f"AirPlay failed: {outcome.get('error')}", hint=f"Details are in {state.LOG}")
            raise typer.Exit(1)
        ui.ok(f"Playing on [key]{device['name']}[/] in the background. [muted]Stop it with: couchcast-helper stop[/]")
        return
    asyncio.run(_play_here(device, resolved, position, rate))


async def _play_here(device: dict, resolved: stream.Stream, position: float, rate: float) -> None:
    loop = asyncio.get_running_loop()
    # Register like the background player, so the next cast (from Firefox or here) takes over
    # this one instead of two AirPlay sessions fighting over the TV.
    session = player.Session(resolved.title, device, position, rate=rate)
    took_over: list[bool] = []

    def on_sigterm() -> None:
        took_over.append(True)
        log.info("[cli] play got SIGTERM: another cast is taking over")
        main_task.cancel()

    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    storage = await airplay.open_storage(loop)
    with ui.step(f"Connecting to {device['name']}…"):
        conf = await airplay.find_tv(loop, storage, device)

    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=None, complete_style=ui.ACCENT, finished_style="green", pulse_style=ui.ACCENT),
        TextColumn("[bold]{task.fields[clock]}"),
        TextColumn("[muted]Ctrl-C stops[/]"),
        console=ui.console,
        expand=True,
    )
    started = time.monotonic()
    heard_from_tv = False
    with progress:
        task = progress.add_task(f"[muted]Starting on {device['name']}…[/]", total=None, clock="")

        def on_started() -> None:
            session.started()
            progress.update(task, description=f"[accent]▶[/] Playing on [key]{device['name']}[/]")

        def on_status(pos: float | None, total: float | None, state_name: str | None) -> None:
            nonlocal heard_from_tv
            heard_from_tv = True
            session.on_status(pos, total, state_name)
            total = total or resolved.duration
            verb = "Paused on" if state_name == "Paused" else "Playing on"
            icon = "⏸" if state_name == "Paused" else "▶"
            progress.update(
                task,
                description=f"[accent]{icon}[/] {verb} [key]{device['name']}[/]",
                total=total,
                completed=pos or 0,
                clock=f"{ui.clock(pos)} / {ui.clock(total)}",
            )

        async def tick() -> None:
            # Until (or unless) the TV reports its position, show honest elapsed time instead.
            while True:
                await asyncio.sleep(1)
                if not heard_from_tv:
                    progress.update(task, clock=f"{ui.clock(time.monotonic() - started)} elapsed")

        ticker = asyncio.ensure_future(tick())
        try:
            await airplay.play(loop, storage, conf, resolved.url, position, on_started, on_status, rate=rate)
        except asyncio.CancelledError:
            session.mark("stopped")
            if not took_over:
                raise
        finally:
            ticker.cancel()
    if took_over:
        ui.console.print("[muted]■ Another cast took over the TV.[/]")
        return
    session.mark("ended")
    ui.ok("Finished.")


@app.command()
@guarded
def stop() -> None:
    """Stop a background cast (from the extension or play --background)."""
    now = player.current()
    if player.stop():
        ui.ok(f"Stopped [key]{now.get('title') or 'playback'}[/] on {now.get('device')}.")
    else:
        ui.console.print("[muted]Nothing is playing.[/]")


@app.command()
@guarded
def install() -> None:
    """Connect the helper to Firefox (registers it as a native app)."""
    target = firefox_manifest()
    if target is None:
        ui.error(f"Don't know where Firefox looks for native apps on {sys.platform}.")
        raise typer.Exit(1)
    wrapper = Path(__file__).resolve().parents[2] / "bin" / "couchcast-host"
    manifest = {
        "name": HOST_NAME,
        "description": "Couchcast helper: plays YouTube on an Apple TV over AirPlay",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2))
    log.info("[install] registered %s -> %s", target, wrapper)
    ui.ok("Connected to Firefox.")
    ui.console.print(f"  [muted]{target}[/]")
    ui.console.print("  Next: in Couchcast's settings in Firefox, choose [key]No ads, straight to Apple TV[/].")


@app.command()
@guarded
def status() -> None:
    """Show what's set up and what's playing."""
    import yt_dlp

    device = state.read_json(state.CONFIG).get("device")
    target = firefox_manifest()
    now = player.current()
    playing = now.get("alive") and now.get("state") in {"starting", "playing"}

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="muted")
    grid.add_column()
    grid.add_row(
        "Apple TV",
        f"[key]{device['name']}[/] [muted]{device.get('address', '')}[/]"
        if device
        else "[warn]not paired[/]  [muted]run: couchcast-helper pair[/]",
    )
    grid.add_row(
        "Firefox",
        "[ok]connected[/]" if target and target.exists() else "[warn]not connected[/]  [muted]run: couchcast-helper install[/]",
    )
    grid.add_row(
        "yt-dlp",
        f"{yt_dlp.version.__version__}  "
        + ("[ok]deno found[/]" if shutil.which("deno") else "[warn]deno missing[/] [muted]yt-dlp needs it for YouTube[/]"),
    )
    grid.add_row("Playing", f"[key]{now.get('title') or 'a video'}[/] on {now.get('device')}" if playing else "[muted]nothing[/]")
    grid.add_row("Log", str(state.LOG))
    ui.console.print(
        Panel(grid, title=f"[accent]couchcast helper[/] [muted]{__version__}[/]", title_align="left", border_style=ui.ACCENT, expand=False)
    )


@app.command(hidden=True, context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def host() -> None:
    """Firefox native messaging host. Firefox appends the manifest path and the caller's add-on id."""
    raise typer.Exit(native.serve())


@app.command("player", hidden=True)
def player_process(
    url: Annotated[str, typer.Option()],
    position: Annotated[float, typer.Option()] = 0,
    title: Annotated[str, typer.Option()] = "",
    video_id: Annotated[Optional[str], typer.Option("--video-id")] = None,
    rate: Annotated[float, typer.Option()] = 1.0,
    launchd_label: Annotated[Optional[str], typer.Option("--launchd-label")] = None,
) -> None:
    device = state.read_json(state.CONFIG)["device"]
    raise typer.Exit(player.run(url, position, title, device, video_id=video_id, rate=rate, launchd_label=launchd_label))


def friendly(error: Exception) -> str:
    from pyatv import exceptions as atv

    if isinstance(error, atv.PairingError):
        return f"Pairing failed: {error}"
    if isinstance(error, atv.AuthenticationError):
        return "The Apple TV turned down Couchcast's credentials. Pair again with: couchcast-helper pair"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "The Apple TV took too long to answer."
    if isinstance(error, (LookupError, RuntimeError, stream.StreamError)):
        return str(error)
    return f"{type(error).__name__}: {error}"


def main() -> None:
    app()

if __name__ == "__main__":
    main()
