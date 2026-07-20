from agents.base import AgentRunner
from agents.investigator import INVESTIGATOR_MODEL
from agents.reviewer import REVIEWER_MODEL
from fastmcp import Client
from mcp_server.server import mcp
from orchestrator.config import SETTINGS, Settings, load_settings
from orchestrator.pipeline import OPENROUTER_BASE_URL
from tests.agent_stubs import StubAsyncOpenAI, make_completion


def test_defaults_match_confirmed_values():
    settings = load_settings()
    assert settings == Settings(
        investigator_model="anthropic/claude-haiku-4.5",
        reviewer_model="anthropic/claude-sonnet-4.5",
        openrouter_base_url="https://openrouter.ai/api/v1",
        temperature=0.0,
        max_tool_iterations=10,
        max_investigator_attempts=3,
        openrouter_timeout_seconds=60.0,
        max_transport_attempts=3,
        retry_backoff_base_seconds=1.0,
    )


def test_load_settings_honors_env_var_overrides(monkeypatch):
    monkeypatch.setenv("INVESTIGATOR_MODEL", "test/investigator-override")
    monkeypatch.setenv("REVIEWER_MODEL", "test/reviewer-override")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_TEMPERATURE", "0.5")
    monkeypatch.setenv("MAX_TOOL_ITERATIONS", "20")
    monkeypatch.setenv("MAX_INVESTIGATOR_ATTEMPTS", "7")
    monkeypatch.setenv("OPENROUTER_TIMEOUT_SECONDS", "30.0")
    monkeypatch.setenv("MAX_TRANSPORT_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BACKOFF_BASE_SECONDS", "2.0")

    settings = load_settings()

    assert settings == Settings(
        investigator_model="test/investigator-override",
        reviewer_model="test/reviewer-override",
        openrouter_base_url="https://example.invalid/v1",
        temperature=0.5,
        max_tool_iterations=20,
        max_investigator_attempts=7,
        openrouter_timeout_seconds=30.0,
        max_transport_attempts=5,
        retry_backoff_base_seconds=2.0,
    )


def test_consumer_modules_reexport_the_singleton_not_a_hardcoded_copy():
    """Regression guard: agents/investigator.py, agents/reviewer.py, and
    orchestrator/pipeline.py must read their model slug / base URL from orchestrator.config's
    SETTINGS singleton rather than re-hardcoding the value, so a future env-var override
    actually takes effect everywhere instead of silently missing one call site."""
    assert INVESTIGATOR_MODEL == SETTINGS.investigator_model
    assert REVIEWER_MODEL == SETTINGS.reviewer_model
    assert OPENROUTER_BASE_URL == SETTINGS.openrouter_base_url


async def test_agent_runner_defaults_come_from_settings(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI([make_completion(content="Done.")])

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        await runner.run("Investigate claim CLM-001.")

    assert stub.requests[0]["temperature"] == SETTINGS.temperature
