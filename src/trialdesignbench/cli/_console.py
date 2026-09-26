from __future__ import annotations

from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape

console = Console(soft_wrap=True)
err_console = Console(stderr=True, soft_wrap=True)


def fail(message: str, code: int = 1) -> NoReturn:
    err_console.print(f"[bold red]error:[/bold red] {escape(message)}")
    raise typer.Exit(code)


def warn(message: str) -> None:
    err_console.print(f"[bold yellow]warning:[/bold yellow] {escape(message)}")
