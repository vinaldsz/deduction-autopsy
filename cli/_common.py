import logging
import os
import sys

from rich.console import Console

from orchestrator.config import SETTINGS


def ensure_api_key(console: Console) -> bool:
    """Print an error and return False if OPENROUTER_API_KEY is missing from the environment."""
    if "OPENROUTER_API_KEY" in os.environ:
        return True
    console.print("[bold red]OPENROUTER_API_KEY is not set[/] — add it to .env or export it.")
    return False


def configure_logging(level: str | None = None) -> None:
    """Configure structured logging to stderr for CLI/non-interactive runs.

    Handlers live at the entrypoint, not in library modules (orchestrator/pipeline.py,
    agents/base.py, which only emit via logging.getLogger(__name__)). Logs go to stderr so
    they never interleave with the rich verdict output on stdout. Level defaults to
    SETTINGS.log_level (LOG_LEVEL env var, default INFO).
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=(level or SETTINGS.log_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
