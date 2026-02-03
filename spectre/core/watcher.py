"""Playwright-based network traffic watcher."""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    Request,
    Response,
    async_playwright,
)
from rich.console import Console
from rich.logging import RichHandler

from spectre.config import get_config
from spectre.database import DatabaseConnection, insert_capture

logger = logging.getLogger(__name__)


JSON_CONTENT_TYPES = {
    "application/json",
    "application/vnd.api+json",
    "text/json",
    "application/json; charset=utf-8",
    "application/json;charset=utf-8",
}


IGNORED_DOMAINS = {
    "google-analytics.com",
    "facebook.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "googletagmanager.com",
    "analytics.google.com",
    "adsystem.google.com",
}


def is_json_response(content_type: Optional[str]) -> bool:
    """Check if the content‑type indicates JSON."""
    if not content_type:
        return False
    content_type = content_type.lower().split(";")[0].strip()
    return content_type in JSON_CONTENT_TYPES


def should_ignore_domain(url: str) -> bool:
    """Determine whether a URL belongs to an ignored domain."""
    try:
        domain = urlparse(url).netloc.lower()
        for ignored in IGNORED_DOMAINS:
            if domain.endswith(ignored):
                return True
        return False
    except (ValueError, AttributeError):
        return False


class Watcher:
    """Main watcher class managing browser session and capture logic."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        headless: bool = True,
        database_path: Optional[str] = None,
    ):
        self.session_id = session_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.headless = headless
        self.database_path = database_path
        self._stop_event = asyncio.Event()

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._running = False
        self._capture_count = 0

        self.console = Console()

    async def start(self, start_url: Optional[str] = None) -> None:
        """Launch browser, set up interception, and optionally navigate."""
        self._running = True
        self._playwright = await async_playwright().start()

        self.console.print(
            f"[bold green]Starting watcher (session: {self.session_id})[/bold green]"
        )
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page()
        self._page.on("close", lambda p: self._stop_event.set())

        await self._page.route("**/*", self._on_route)

        if start_url:
            self.console.print(f"[cyan]Navigating to {start_url}[/cyan]")
            await self._page.goto(start_url)

        self.console.print("[yellow]Watcher is active. Press Ctrl+C to stop.[/yellow]")

    async def stop(self) -> None:
        """Gracefully shut down the watcher."""
        self._running = False
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.console.print(
            f"[bold green]Watcher stopped. Captured {self._capture_count} responses.[/bold green]"
        )

    async def _on_route(self, route, request: Request) -> None:
        """Intercept every request and let it proceed."""
        await route.continue_()

    async def _on_response(self, response: Response) -> None:
        """Process a network response."""
        if not self._running:
            return

        url = response.url
        method = response.request.method

        if should_ignore_domain(url):
            logger.debug(f"Ignoring domain: {url}")
            return

        headers = response.headers
        content_type = headers.get("content-type")
        if not is_json_response(content_type):
            return

        try:
            body = await response.body()
        except Exception as e:
            logger.warning(f"Failed to read body from {url}: {e}")
            return

        try:
            json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug(f"Non‑JSON body in {url}, skipping")
            return

        body_hash = hashlib.sha256(body).hexdigest()

        try:
            with DatabaseConnection(self.database_path) as conn:
                insert_capture(
                    conn,
                    session_id=self.session_id,
                    url=url,
                    method=method,
                    headers=dict(headers),
                    status=response.status,
                    body=body,
                    timestamp=datetime.utcnow().isoformat(),
                )
        except Exception as e:
            logger.error(f"Database insert failed for {url}: {e}")
            return

        self._capture_count += 1
        logger.info(f"Captured {method} {url} ({len(body)} bytes)")

        if self._capture_count % 10 == 0:
            self.console.print(
                f"[dim]Captured {self._capture_count} responses so far...[/dim]"
            )

    async def capture(self, url: str) -> None:
        """
        Navigate to a URL and wait for page load, capturing responses.
        """
        if not self._page:
            raise RuntimeError("Watcher not started")
        self.console.print(f"[cyan]Capturing from {url}[/cyan]")
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            self.console.print(f"[yellow]Navigation warning (non-fatal): {e}[/yellow]")
        await asyncio.sleep(3)

    async def run_until_interrupt(self) -> None:
        """Keep the watcher running until stop event is set."""
        try:
            self._page.on("response", self._on_response)
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


async def watch_url(
    url: str,
    session_id: Optional[str] = None,
    headless: bool = True,
    database_path: Optional[str] = None,
) -> None:
    """
    High‑level coroutine to watch a single URL.

    This is the main entry point for the CLI command `spectre watch`.
    """
    watcher = Watcher(
        session_id=session_id,
        headless=headless,
        database_path=database_path,
    )
    try:
        await watcher.start(start_url=url)
        await watcher.run_until_interrupt()
    except KeyboardInterrupt:
        await watcher.stop()
    except Exception as e:
        logger.error(f"Watcher crashed: {e}")
        await watcher.stop()
        raise


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with Rich handler."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )

    logging.getLogger("playwright").setLevel(logging.WARNING)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(watch_url("https://jsonplaceholder.typicode.com/posts"))
