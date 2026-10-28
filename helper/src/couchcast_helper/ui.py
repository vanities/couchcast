import re

from rich.console import Console
from rich.prompt import Prompt
from rich.theme import Theme

ACCENT = "#9b85ff"

console = Console(
    theme=Theme(
        {
            "accent": f"bold {ACCENT}",
            "ok": "bold green",
            "err": "bold red",
            "warn": "yellow",
            "key": "bold",
            "muted": "dim",
        }
    ),
    highlight=False,
)


def ok(message: str) -> None:
    console.print(f"[ok]✓[/] {message}")


def warn(message: str, hint: str | None = None) -> None:
    console.print(f"[warn]![/] {message}")
    if hint:
        console.print(f"  [muted]{hint}[/]")


def error(message: str, hint: str | None = None) -> None:
    console.print(f"[err]✗[/] {message}")
    if hint:
        console.print(f"  [muted]{hint}[/]")


def step(text: str):
    """Spinner for a wait. Use as a context manager."""
    return console.status(f"[accent]{text}[/]", spinner="dots", spinner_style=ACCENT)


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def ask_pin() -> int:
    """Re-asks until it gets 4 digits; spaces and dashes are fine."""
    while True:
        digits = re.sub(r"\D", "", Prompt.ask("[accent]?[/] PIN shown on the TV", console=console))
        if len(digits) == 4:
            return int(digits)
        error("The PIN is the 4 digits on the TV screen.")


def ask_choice(prompt: str, options: list[str]) -> int:
    for number, option in enumerate(options, 1):
        console.print(f"  [accent]{number}[/] {option}")
    while True:
        answer = Prompt.ask(f"[accent]?[/] {prompt}", console=console, default="1")
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        error(f"Pick a number from 1 to {len(options)}.")
