"""The HyDE API backend's Qwen provider.

HyDE is the only part of the server that calls a model API, and it is opt-in: the
default is rule-based expansion, and nothing here runs unless ``HYDE_BACKEND=api``
and ``PATENT_MPEP_USE_HYDE=true``. These tests keep provider selection, the
per-provider key and the documented default model agreeing with each other, since
the README, the config schema, .env.example and hyde.py each state the default
separately.

No network calls: the OpenAI SDK is replaced with a recording stub.
"""

import importlib
import pathlib
import sys
import types

import pytest

from mcp_server import config as cfg
from mcp_server import hyde

# Imported without loading the embedding models: the tests below only exercise the
# HyDE branch, so the search object is built with __new__ rather than __init__.
mpep_search = importlib.import_module("mcp_server.mpep_search")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Clear every API variable so a developer's real key cannot leak into a run."""
    for key in (
        "QWEN_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_BASE_URL",
        "HYDE_BACKEND",
        "HYDE_API_PROVIDER",
        "HYDE_QWEN_MODEL",
        "HYDE_OPENAI_BASE_URL",
        "HYDE_STRICT",
    ):
        monkeypatch.delenv(key, raising=False)
    # Values are only read as environment variables here, but pin the config store
    # anyway so a saved config file cannot supply a key or model.
    monkeypatch.setenv("PATENT_CONFIG_DIR", "isolated")


class FakeOpenAI:
    """Stand-in for the OpenAI SDK that records what it was built with and called with."""

    def __init__(self, api_key=None, base_url=None, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self.request = {}
        self.fail = False

        def create(**kwargs):
            self.request.update(kwargs)
            if self.fail:
                raise RuntimeError("the API returned 401")
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="generated text"))]
            )

        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))


@pytest.fixture
def fake_openai(monkeypatch):
    module = types.ModuleType("openai")
    module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return module


@pytest.fixture
def no_local_model(monkeypatch):
    """Make the local-model branch unavailable, so `auto` decides between API and rules only."""

    def fail(self):
        raise ValueError("local model disabled for this test")

    monkeypatch.setattr(hyde.HyDEQueryExpander, "_init_local_backend", fail)


class TestProviderSelection:
    def test_qwen_key_wins_when_more_than_one_key_is_set(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert hyde.HyDEQueryExpander("api").api_provider == "qwen"

    def test_a_qwen_key_alone_selects_qwen(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        assert hyde.HyDEQueryExpander("api").api_provider == "qwen"

    def test_the_selected_provider_is_the_backend_that_runs(self, monkeypatch, fake_openai):
        # expand_query() dispatches on backend, not api_provider, so a backend left
        # as "api" makes every call return the query unchanged.
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        expander = hyde.HyDEQueryExpander("api")
        assert expander.backend == "qwen"
        result = expander.expand_query("antecedent basis")
        assert len(result) > 1, "the API path was not reached"
        assert expander.api_client.request.get("model") == hyde.DEFAULT_QWEN_MODEL

    def test_without_any_key_the_backend_is_not_silently_available(self):
        # An unset key must be an error, not a quiet downgrade to rule-based: setting
        # HYDE_BACKEND=api is a request for API expansion.
        with pytest.raises(ValueError, match="HyDE API backend is unavailable"):
            hyde.HyDEQueryExpander("api")

    def test_qwen_uses_the_openai_protocol_so_no_extra_sdk_is_needed(self):
        assert "qwen" in hyde.API_PROVIDERS
        assert hyde.PROVIDER_KEY_ENV["qwen"] == "QWEN_API_KEY"


class TestKeysAreNeverSentAcrossProviders:
    def test_the_qwen_endpoint_gets_the_qwen_key(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert hyde.HyDEQueryExpander("api").api_client.api_key == "sk-qwen"

    def test_the_openai_endpoint_gets_the_openai_key(self, monkeypatch, fake_openai):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert hyde.HyDEQueryExpander("api").api_client.api_key == "sk-openai"

    def test_an_explicit_provider_does_not_borrow_another_provider_s_key(self, monkeypatch, fake_openai):
        monkeypatch.setenv("HYDE_API_PROVIDER", "qwen")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        with pytest.raises(ValueError, match="QWEN_API_KEY is not set"):
            hyde.HyDEQueryExpander("api")


class TestTheBaseURL:
    def test_qwen_defaults_to_the_dashscope_compatible_endpoint(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        assert hyde.HyDEQueryExpander("api").api_client.base_url == hyde.DEFAULT_QWEN_BASE_URL

    def test_hyde_openai_base_url_wins_for_a_local_serve(self, monkeypatch, fake_openai):
        # A local vLLM/Ollama serve is the keyless path people actually use, so the
        # documented override has to beat the Qwen default.
        monkeypatch.setenv("QWEN_API_KEY", "local")
        monkeypatch.setenv("HYDE_OPENAI_BASE_URL", "http://localhost:11434/v1")
        assert hyde.HyDEQueryExpander("api").api_client.base_url == "http://localhost:11434/v1"

    def test_the_openai_provider_keeps_the_openai_endpoint(self, monkeypatch, fake_openai):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert hyde.HyDEQueryExpander("api").api_client.base_url is None


class TestTheDefaultModelAgreesWithTheDocumentation:
    def test_the_code_default_config_default_readme_and_env_example_say_the_same_thing(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert f"Default model `{hyde.DEFAULT_QWEN_MODEL}`" in readme
        assert cfg.OPTIONS_BY_KEY["HYDE_QWEN_MODEL"].default == hyde.DEFAULT_QWEN_MODEL
        # A copied .env.example line should not quietly cost more than the documented default.
        assert f"# HYDE_QWEN_MODEL={hyde.DEFAULT_QWEN_MODEL}" in env_example

    def test_the_env_override_still_works(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("HYDE_QWEN_MODEL", "qwen3-coder-plus")
        expander = hyde.HyDEQueryExpander("api")
        expander.expand_query("antecedent basis")
        assert expander.api_client.request["model"] == "qwen3-coder-plus"

    def test_the_default_model_reaches_the_request_when_no_override_is_set(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        expander = hyde.HyDEQueryExpander("api")
        expander.expand_query("antecedent basis")
        assert expander.api_client.request["model"] == hyde.DEFAULT_QWEN_MODEL


class TestStrictMode:
    def test_strict_reports_the_failure_instead_of_falling_back_silently(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("HYDE_STRICT", "true")
        expander = hyde.HyDEQueryExpander("api")
        expander.api_client.fail = True
        with pytest.raises(ValueError, match="HyDE API call failed"):
            expander.expand_query("antecedent basis")

    def test_strict_applies_to_the_anthropic_path_too(self):
        # Both API paths report failures through the same helper, so strict mode
        # cannot mean "strict for Qwen only".
        source = (REPO_ROOT / "mcp_server" / "hyde.py").read_text(encoding="utf-8")
        for name in ("_anthropic_expansion", "_openai_expansion"):
            body = source.split(f"def {name}(")[1].split("\n    def ")[0]
            assert "self._api_failure(" in body, name

    def test_without_strict_the_failure_degrades_to_rule_based(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        expander = hyde.HyDEQueryExpander("api")
        expander.api_client.fail = True
        result = expander.expand_query("antecedent basis")
        assert result and result[0] == "antecedent basis"
        assert expander.backend == "qwen"

    def test_a_strict_failure_at_initialization_is_not_swallowed(self, monkeypatch, no_local_model):
        # The search engine catches an expander that cannot be built, so strict mode has to reach
        # past that catch: otherwise HYDE_STRICT reads as "strict for calls, quiet for setup", and
        # a user who asked for failures to be visible gets search without HyDE.
        monkeypatch.setenv("HYDE_BACKEND", "api")
        monkeypatch.setenv("HYDE_STRICT", "true")
        engine = mpep_search.MPEPIndex.__new__(mpep_search.MPEPIndex)
        engine.hyde_expander = None
        with pytest.raises(ValueError, match="HyDE API backend is unavailable"):
            engine._init_hyde()

    def test_without_strict_an_expander_that_cannot_be_built_still_searches(self, monkeypatch, no_local_model):
        # The catch is what makes HyDE optional, so it has to keep working when strict is off.
        # Same setup as the test above, minus HYDE_STRICT: an opted-in API that cannot be built
        # degrades to rule-based expansion instead of stopping the search.
        monkeypatch.setenv("HYDE_BACKEND", "api")
        engine = mpep_search.MPEPIndex.__new__(mpep_search.MPEPIndex)
        engine.hyde_expander = None
        assert engine._init_hyde() is False
        assert engine.hyde_expander is None

    def test_the_strict_answer_is_the_same_one_the_expander_uses(self, monkeypatch):
        # The caller decides whether a HyDE failure is swallowed, so it has to answer the same
        # question the expander answers, and it has to be able to ask before an expander exists.
        monkeypatch.setenv("HYDE_STRICT", "yes")
        assert mpep_search._hyde_strict is not None
        assert mpep_search._hyde_strict() is True and hyde.strict_enabled() is True

    def test_a_strict_failure_is_still_reported_when_the_import_of_the_expander_fails(self, monkeypatch):
        # If the handler asked about strict mode through a name imported inside its own try block,
        # a failed import would replace the real HyDE failure with a NameError and the caller would
        # see the wrong reason. The check is bound at module level, so it is available here.
        monkeypatch.setenv("HYDE_STRICT", "true")
        for name in ("hyde", "mcp_server.hyde"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        engine = mpep_search.MPEPIndex.__new__(mpep_search.MPEPIndex)
        engine.hyde_expander = None
        with pytest.raises(ImportError):
            engine._init_hyde()

    def test_auto_is_an_accepted_provider_choice(self):
        # The documented way to leave provider selection to key order is an empty
        # value, but README and .env.example also show auto, so the schema has to
        # accept the spelling it advertises.
        assert "auto" in cfg.OPTIONS_BY_KEY["HYDE_API_PROVIDER"].choices


class TestTheSearchCallerKeepsTheStrictFailure:
    """Strict mode is only useful if the caller does not catch what it raises.

    The search path had a bare `except Exception` around expansion, which turned a
    strict failure into results from the unexpanded query, so HYDE_STRICT looked
    like it worked while silently returning the fallback.
    """

    def engine(self, expander):
        engine = mpep_search.MPEPIndex.__new__(mpep_search.MPEPIndex)
        engine.use_hyde = True
        engine.hyde_expander = expander
        return engine

    def test_a_strict_failure_reaches_the_search_caller(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        monkeypatch.setenv("HYDE_STRICT", "true")
        expander = hyde.HyDEQueryExpander("api")
        expander.api_client.fail = True
        with pytest.raises(ValueError, match="HyDE API call failed"):
            self.engine(expander)._expand_queries("antecedent basis")

    def test_a_non_strict_failure_still_returns_expansions(self, monkeypatch, fake_openai):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
        expander = hyde.HyDEQueryExpander("api")
        expander.api_client.fail = True
        result = self.engine(expander)._expand_queries("antecedent basis")
        assert result[0] == "antecedent basis"
        assert len(result) > 1

    def test_an_error_the_expander_does_not_handle_falls_back_in_the_caller(self):
        # A raising expander is what the caller's fallback is for; it must keep the
        # original query rather than returning nothing.
        def boom(query, num_expansions=3):
            raise RuntimeError("sdk blew up")

        engine = self.engine(types.SimpleNamespace(expand_query=boom, strict=False))
        assert engine._expand_queries("antecedent basis") == ["antecedent basis"]

    def test_hyde_off_returns_only_the_original_query(self):
        engine = mpep_search.MPEPIndex.__new__(mpep_search.MPEPIndex)
        engine.use_hyde = False
        engine.hyde_expander = None
        assert engine._expand_queries("antecedent basis") == ["antecedent basis"]
