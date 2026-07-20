import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    investigator_model: str
    reviewer_model: str
    openrouter_base_url: str
    temperature: float
    max_tool_iterations: int
    max_investigator_attempts: int
    openrouter_timeout_seconds: float
    max_transport_attempts: int
    retry_backoff_base_seconds: float


def load_settings() -> Settings:
    """Read settings from the environment, falling back to the confirmed defaults.

    Called once at import time to produce the module-level SETTINGS singleton that
    agents/investigator.py, agents/reviewer.py, agents/base.py, and orchestrator/pipeline.py
    all import from — see docs/PLAN.md's Layer 12. Exposed separately (rather than only the
    singleton) so tests can construct Settings against arbitrary env vars without relying on
    import order.
    """
    return Settings(
        investigator_model=os.environ.get("INVESTIGATOR_MODEL", "anthropic/claude-haiku-4.5"),
        reviewer_model=os.environ.get("REVIEWER_MODEL", "anthropic/claude-sonnet-4.5"),
        openrouter_base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.0")),
        max_tool_iterations=int(os.environ.get("MAX_TOOL_ITERATIONS", "10")),
        max_investigator_attempts=int(os.environ.get("MAX_INVESTIGATOR_ATTEMPTS", "3")),
        openrouter_timeout_seconds=float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "60.0")),
        max_transport_attempts=int(os.environ.get("MAX_TRANSPORT_ATTEMPTS", "3")),
        retry_backoff_base_seconds=float(
            os.environ.get("RETRY_BACKOFF_BASE_SECONDS", "1.0")
        ),
    )


SETTINGS = load_settings()
