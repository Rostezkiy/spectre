"""Command-line interface for Spectre."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from spectre.config import get_config, reload_config
from spectre.core.analyzer import analyze_database, print_analysis
from spectre.core.watcher import setup_logging as setup_watcher_logging
from spectre.core.watcher import Watcher
from spectre.database import DatabaseConnection, init_database

app = typer.Typer(
    help="Spectre: Local-first API generator via headless browser capture.",
    no_args_is_help=True,
)
console = Console()

# Configure logging for CLI
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)

async def wait_for_enter(stop_event: asyncio.Event):
    await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
    stop_event.set()

@app.command()
def watch(
    url: str = typer.Argument(..., help="URL to watch for JSON traffic"),
    session_id: Optional[str] = typer.Option(
        None, "--session-id", "-s", help="Session identifier for grouping"
    ),
    headless: bool = typer.Option(
        True, "--headless/--visible", help="Run browser in headless mode"
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to configuration YAML"
    ),
):
    """Start capturing JSON responses from a website."""
    if config:
        import os
        os.environ["SPECTRE_CONFIG_PATH"] = str(config)

    reload_config()
    cfg = get_config()

    console.print(f"[bold green]Starting watcher for {url}[/bold green]")
    console.print(f"Database: {cfg.database_path}")

    init_database(cfg.database_path)

    try:
        watcher = Watcher(
            session_id=session_id,
            headless=headless,
            database_path=cfg.database_path,
        )
        
        async def run_all():
            await watcher.start(start_url=url)
            
            enter_task = asyncio.create_task(wait_for_enter(watcher._stop_event))
            
            console.print("\n[bold yellow]─── WATCHING MODE ACTIVE ───[/bold yellow]")
            console.print("[bold cyan]• Intercepting JSON traffic in background...[/bold cyan]")
            console.print("[bold green]• Click links in the browser to capture more data.[/bold green]")
            console.print("[bold yellow]• Press ENTER in this terminal to stop safely.[/bold yellow]\n")
            
            await watcher.run_until_interrupt()
            
            if not enter_task.done():
                enter_task.cancel()

        asyncio.run(run_all())

    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher interrupted by Ctrl+C. Saving data and exiting...[/yellow]")
    except Exception as e:
        console.print(f"[red]Watcher failed: {e}[/red]")
        sys.exit(1)


@app.command()
def analyze(
    generate_config: bool = typer.Option(
        False, "--generate-config", "-g", help="Generate YAML configuration"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write YAML to file"
    ),
    limit: int = typer.Option(
        1000, "--limit", "-l", help="Maximum distinct URLs to analyze"
    ),
):
    """Analyze captured URLs and suggest resources."""
    cfg = get_config()
    console.print(f"[bold]Analyzing database at {cfg.database_path}[/bold]")

    clusters, resources = analyze_database(cfg.database_path, limit)

    if not clusters:
        console.print("[yellow]No captured URLs found.[/yellow]")
        return

    if generate_config or output:
        from spectre.core.analyzer import generate_yaml_config

        yaml_text = generate_yaml_config(resources)
        if output:
            output.write_text(yaml_text, encoding="utf-8")
            console.print(f"[green]Configuration written to {output}[/green]")
        else:
            console.print("\n[bold]Generated YAML:[/bold]")
            console.print(yaml_text)
    else:
        print_analysis(clusters, resources)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(
        False, "--reload", help="Enable auto-reload (development)"
    ),
):
    """Start the FastAPI server."""
    import uvicorn

    from spectre.server.main import app as fastapi_app

    console.print(
        f"[bold green]Starting Spectre server on http://{host}:{port}[/bold green]"
    )
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def db_init(
    database_path: Optional[Path] = typer.Option(
        None, "--database", "-d", help="Path to DuckDB file"
    ),
):
    """Initialize the database schema."""
    path = str(database_path) if database_path else None
    console.print(f"[bold]Initializing database at {path or 'default'}[/bold]")
    try:
        init_database(path)
        console.print("[green]Database schema created successfully.[/green]")
    except Exception as e:
        console.print(f"[red]Failed to initialize database: {e}[/red]")
        sys.exit(1)


@app.command()
def clean(
    older_than_days: int = typer.Option(
        30, "--older-than", help="Delete captures older than X days"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
):
    """Clean up old captures and orphaned blobs."""
    cfg = get_config()
    console.print(
        f"[bold yellow]Cleaning database at {cfg.database_path}[/bold yellow]"
    )
    console.print(
        f"This will delete captures older than {older_than_days} days "
        "and remove unreferenced blobs."
    )

    if not yes:
        confirm = typer.confirm("Are you sure?")
        if not confirm:
            console.print("[dim]Aborted.[/dim]")
            return

    with DatabaseConnection(cfg.database_path) as conn:
        from spectre.database import cleanup_old_captures

        deleted = cleanup_old_captures(conn, older_than_days)
        console.print(f"[green]Deleted {deleted} old captures.[/green]")


@app.command()
def version():
    """Show Spectre version."""
    from spectre import __version__

    console.print(f"[bold cyan]Spectre v{__version__}[/bold cyan]")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()