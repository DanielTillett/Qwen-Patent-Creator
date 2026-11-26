"""The core promise of the fork: the MCP server is client-agnostic and offline by default.

Nothing in `mcp_server` calls a model API unless the user opts in. That is why any MCP client
running Qwen, Claude or another model works with the server unchanged, and it is the reason the
plugin layer is prompts rather than code. If a future change adds a model call somewhere else,
these tests fail rather than let the claim quietly become false.

The local-model backend is stubbed so a run does not load distilgpt2; the point being tested is
which backend `auto` chooses, not that a model can be loaded.
"""

import re
import sys
import types
from pathlib import Path

import pytest

from mcp_server import config, hyde

REPO_ROOT = Path(__file__).resolve().parent.parent

LLM_IMPORT = re.compile(r"^\s*import\s+(openai|anthropic)\b", re.MULTILINE)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Clear every API variable so a developer's real key cannot leak into a run."""
    for key in ("HYDE_BACKEND", "HYDE_API_PROVIDER") + tuple(hyde.PROVIDER_KEY_ENV.values()):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def no_local_model(monkeypatch):
    """Make the local-model branch unavailable, so `auto` decides between API and rules only."""

    def fail(self):
        raise ValueError("local model disabled for this test")

    monkeypatch.setattr(hyde.HyDEQueryExpander, "_init_local_backend", fail)


class TestNoModelCallHappensByDefault:
    def test_the_hyde_default_is_off(self):
        assert config.hyde_enabled() is False

    def test_auto_does_not_use_an_api_just_because_a_key_is_present(self, no_local_model):
        # A Qwen user often has QWEN_API_KEY set for other reasons; having a key must not
        # silently spend quota on query expansion. Opt-in is HYDE_BACKEND=api.
        expander = hyde.HyDEQueryExpander("auto")
        assert expander.backend == "rule-based"
        assert expander.api_client is None

    def test_expansion_runs_without_an_api_client(self, no_local_model):
        expander = hyde.HyDEQueryExpander("auto")
        query = "obviousness rejection combination of references"
        expansions = expander.expand_query(query)
        assert expansions[0] == query
        assert len(expansions) > 1

    def test_an_api_call_only_happens_after_an_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("HYDE_BACKEND", "api")
        stub = types.ModuleType("openai")
        stub.OpenAI = lambda **kwargs: types.SimpleNamespace(api_key=kwargs.get("api_key"))
        monkeypatch.setitem(sys.modules, "openai", stub)
        expander = hyde.HyDEQueryExpander("auto")
        assert expander.backend == "qwen"
        assert expander.api_client is not None
        assert expander.api_client.api_key == "sk-qwen"


class TestOnlyHydeCanReachAnApi:
    """Every other module is retrieval and rules, which is what makes the server portable."""

    def test_no_other_module_imports_an_llm_sdk(self):
        offenders = []
        for path in sorted((REPO_ROOT / "mcp_server").glob("*.py")):
            if path.name == "hyde.py":
                continue
            if LLM_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_hyde_imports_the_sdks_only_when_a_call_is_requested(self):
        source = (REPO_ROOT / "mcp_server" / "hyde.py").read_text(encoding="utf-8")
        # The imports sit inside the provider setup, so an offline server never needs them.
        setup = source.index("def _init_api_backend")
        offenders = [m.group(1) for m in LLM_IMPORT.finditer(source) if m.start() < setup]
        assert offenders == []
