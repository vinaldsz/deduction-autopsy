import os

from rich.console import Console


def ensure_api_key(console: Console) -> bool:
    """Print an error and return False if OPENROUTER_API_KEY is missing from the environment."""
    if "OPENROUTER_API_KEY" in os.environ:
        return True
    console.print("[bold red]OPENROUTER_API_KEY is not set[/] — add it to .env or export it.")
    return False
