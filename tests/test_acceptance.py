"""The acceptance criteria from docs/QWEN_ADAPTATION_PLAN.md §6, as far as a test can observe them.

Two criteria need something a test run does not have: an installed OpenCode or Qwen Code CLI
to list the server, and a live agent invocation under a Qwen model. Those are marked `manual`
in the plan. What a test can check is the part those clients would act on: the names they
would discover, the files registration writes, the exit codes a run produces, and the policy
a denied tool falls through to. Each test names the criterion it covers.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import tomllib

from mcp_server import cli, config, data_paths, formalities_checker, hyde
from mcp_server import registration as reg

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
SERVER = REPO_ROOT / "mcp_server" / "server.py"
NAME = reg.SERVER_NAME

_spec = importlib.util.spec_from_file_location(
    "build_plugin_targets", REPO_ROOT / "scripts" / "build_plugin_targets.py"
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

LLM_IMPORT = re.compile(r"^\s*import\s+(openai|anthropic)\b", re.MULTILINE)
# OpenCode's sanitiser keeps only these characters, so a server name that stays inside them
# is discovered under the same name setup registers.
OPENCODE_SAFE = re.compile(r"[A-Za-z0-9_-]+")
MCP_TOOL_DECORATOR = re.compile(r"@mcp\.tool\(\)\s*(?:@[^\n]+\n\s*)*(?:async\s+)?def (\w+)")


def pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def mcp_tool_names() -> set[str]:
    names = set()
    for path in (REPO_ROOT / "mcp_server").rglob("*.py"):
        names.update(MCP_TOOL_DECORATOR.findall(path.read_text(encoding="utf-8")))
    return names


def write_entry(client: str, path: Path, entry: dict) -> Path:
    """Write a client config file containing one server entry."""
    node = doc = {}
    for key in reg.section_key(client):
        node = node.setdefault(key, {})
    node[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def rule_lines(frontmatter: str) -> list[dict]:
    """Read a generated agent's `permissions` block as the rules OpenCode would apply."""
    rules, current, in_block = [], None, False
    for line in frontmatter.splitlines():
        if line.startswith("permissions:"):
            in_block = True
            continue
        if not in_block:
            continue
        if line.strip().startswith("- action:"):
            current = {"action": line.strip().split("- action:")[1].strip()}
            rules.append(current)
        elif current is not None and ":" in line:
            key, value = line.strip().split(":", 1)
            current[key] = value.strip()
    return rules


def matches(rule_value: str, wanted: str) -> bool:
    """A rule matches when it is the exact name, a `server_*` group, or a wildcard."""
    value = rule_value.strip('"')
    if value == "*":
        return True
    return value == wanted or (value.endswith("_*") and wanted.startswith(value[:-1]))


def effect_for(frontmatter: str, action: str, resource: str = "*") -> str:
    """OpenCode applies rules on top of an allow-all base policy; the last match wins."""
    effect = "allow"
    for rule in rule_lines(frontmatter):
        if matches(rule["action"], action) and matches(rule.get("resource", '"*"'), resource):
            effect = rule["effect"]
    return effect


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory, so a user-scope path stays inside tmp_path."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


class TestCriterion1TheInstalledIdentity:
    def test_the_command_an_install_provides_is_the_fork_name(self):
        scripts = pyproject()["project"]["scripts"]
        assert list(scripts) == ["qwen-patent-creator"]
        assert scripts["qwen-patent-creator"] == "mcp_server.cli:main"

    def test_the_version_line_reports_the_fork_and_the_pyproject_version(self):
        # An installed copy reports the version pyproject.toml carries: that number is what
        # the CI version guard and Patent Creator Studio compare an install against.
        action = next(a for a in cli.build_parser()._actions if "--version" in a.option_strings)
        assert action.version == "qwen-patent-creator " + pyproject()["project"]["version"]

    def test_the_fork_uses_its_own_data_and_config_folders(self, tmp_path, monkeypatch):
        # Sharing upstream's folder would make two different installs read one corpus,
        # which is what plan §5 decided against.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.delenv("PATENT_DATA_DIR", raising=False)
        monkeypatch.delenv("PATENT_CONFIG_DIR", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert config.APP_DIR_NAME == "qwen-patent-creator"
        assert data_paths._APP_DIR_NAME == "qwen-patent-creator"
        assert "claude" not in data_paths._default_base().as_posix()
        assert "claude" not in config.config_dir().as_posix()


class TestCriterion2ColdInstallWritesNothing:
    def test_client_none_completes_and_writes_no_client_config(self, home, monkeypatch):
        monkeypatch.setenv("PATENT_DATA_DIR", str(home / "data"))
        monkeypatch.setenv("PATENT_CONFIG_DIR", str(home / "config"))
        assert cli.configure_mcp_server("none", "user", home) is True
        for client in reg.CLIENTS:
            if client == "none":
                continue
            assert not reg.config_path(client, "user", home).exists(), client

    def test_an_isolated_data_dir_is_what_the_run_uses(self, home, monkeypatch):
        # The cold-install run must not touch a developer's real corpus or config.
        monkeypatch.setenv("PATENT_DATA_DIR", str(home / "data"))
        monkeypatch.setenv("PATENT_CONFIG_DIR", str(home / "config"))
        assert data_paths.index_dir() == home / "data" / "index"
        assert config.config_dir() == home / "config"

    def test_client_none_exits_zero_when_verified(self, monkeypatch):
        # A run with `--client none` is a complete setup rather than a failure, so verifying
        # it reports nothing to check and returns 0.
        monkeypatch.setattr(sys, "argv", ["qwen-patent-creator", "verify-config", "--client", "none"])
        assert cli.main() == 0


class TestCriterion3AProjectEntryWinsOverTheUserOne:
    def test_a_project_scope_writes_the_project_file_and_not_the_home_one(self, home):
        project = home / "project"
        project.mkdir()
        ok, detail = reg.register("opencode", "project", PYTHON, SERVER, project)
        assert ok, detail
        assert reg.config_path("opencode", "project", project).exists()
        assert not reg.config_path("opencode", "user", home).exists()

    def test_the_entry_a_project_scope_run_reads_is_the_project_one(self, home):
        project = home / "project"
        project.mkdir()

        def entry(suffix):
            return {"type": "local", "command": [str(PYTHON), suffix]}

        user_file = write_entry("opencode", reg.config_path("opencode", "user", home), entry("from-user"))
        project_file = write_entry("opencode", reg.config_path("opencode", "project", project), entry("from-project"))
        # The client resolves a project entry before the global one, so these are the two
        # entries a run could see, and the project file is the one a project scope reads.
        assert reg.read_entry(project_file, "opencode")["command"][1] == "from-project"
        assert reg.read_entry(user_file, "opencode")["command"][1] == "from-user"

    def test_an_existing_project_entry_is_merged_not_replaced(self, home):
        project = home / "project"
        project.mkdir()
        original = {"type": "local", "command": [str(PYTHON)], "env": {"KEEP": "1"}}
        path = write_entry("opencode", reg.config_path("opencode", "project", project), original)
        ok, detail = reg.register("opencode", "project", PYTHON, SERVER, project)
        assert ok, detail
        entry = reg.read_entry(path, "opencode")
        assert entry["env"] == {"KEEP": "1"}, entry


class TestCriterion4TheNamesTheClientsDiscover:
    """A live `opencode mcp list` and Qwen Code's MCP commands need an installed client.

    What can be checked without one is that the names those listings show are the names the
    generator and registration use, because a mismatch there means a permission rule or a
    `tools:` entry never matches anything.
    """

    def test_server_name_is_the_same_in_the_generator_and_in_registration(self):
        assert gen.SERVER_NAME == NAME
        assert OPENCODE_SAFE.fullmatch(NAME), f"{NAME} would be sanitised by OpenCode"

    def test_every_tool_name_survives_the_opencode_sanitiser(self):
        offenders = [t for t in sorted(mcp_tool_names()) if not OPENCODE_SAFE.fullmatch(gen.opencode_action(t))]
        assert offenders == []

    def test_qwen_needs_the_qualified_form_and_opencode_the_action_form(self):
        tool = sorted(mcp_tool_names())[0]
        assert gen.qwen_tool_name(tool) == f"mcp__{NAME}__{tool}"
        assert gen.opencode_action(tool) == f"{NAME}_{tool}"

    def test_the_qwen_skill_name_rule_is_respected(self):
        offenders = [
            path.parent.name
            for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))
            if not gen.QWEN_SKILL_NAME.fullmatch(path.parent.name)
        ]
        assert offenders == []


class TestCriterion5CheckerOutputAndDeniedTools:
    """The live half of this criterion needs a Qwen-run agent; the reason it holds is checkable."""

    def test_checkers_read_no_client_or_model_configuration(self):
        # The same fixture gives the same verdict in any client because a checker has nothing
        # to read about the client: no key, no model, no provider.
        offenders = []
        for path in sorted(REPO_ROOT.glob("mcp_server/*checker*.py")):
            text = path.read_text(encoding="utf-8")
            if LLM_IMPORT.search(text) or "environ" in text or "getenv" in text:
                offenders.append(path.name)
        assert offenders == []

    def test_the_same_fixture_gives_the_same_verdict_under_a_qwen_and_an_anthropic_run(self, monkeypatch):
        fixture = (
            "A data processing system (100) includes a processor (110) connected to a memory (120) "
            "via a bus (130). The processor (110) executes instructions stored in the memory (120) "
            "to perform signal analysis on input data received from a sensor array (140). "
            "The system achieves improved throughput by employing a multi-stage pipeline (150) "
            "with parallel execution units (160) operating on partitioned data streams. "
            "Results are output through an interface module (170) to external devices."
        )
        monkeypatch.setenv("HYDE_API_PROVIDER", "qwen")
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        under_qwen = formalities_checker.FormalitiesChecker().analyze(fixture, "Data processing system")
        monkeypatch.setenv("HYDE_API_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        under_anthropic = formalities_checker.FormalitiesChecker().analyze(fixture, "Data processing system")
        assert under_qwen == under_anthropic

    def test_an_agent_that_needs_a_tool_it_does_not_list_is_denied(self):
        # A canonical allowlist becomes a deny-all-first policy for OpenCode, so a tool the
        # agent does not list is refused and the permission error names it.
        source = {"name": "an-agent", "description": "Description.", "tools": "read, write"}
        frontmatter = gen.opencode_agent(source, "Body")
        assert effect_for(frontmatter, "read") == "allow"
        assert effect_for(frontmatter, "grep") == "deny"
        assert effect_for(frontmatter, "subagent") == "deny"
        assert effect_for(frontmatter, f"{NAME}_review_patent_claims") == "allow"
        # The `.env` rule is added after the allow block, so it is the last match for a
        # credential file and the run asks instead of reading it.
        assert effect_for(frontmatter, "read", "*.env") == "ask"

    def test_qwen_refuses_a_tool_that_is_not_in_the_agents_list(self):
        source = {"name": "an-agent", "description": "Description.", "tools": "read, review_patent_claims"}
        listed = gen.qwen_agent(source, "Body").split("---")[1]
        assert f"mcp__{NAME}__review_patent_claims" in listed
        assert "run_shell_command" not in listed


class TestCriterion6HyDEIsCheckedWithoutALiveKey:
    def test_the_default_model_agrees_across_code_and_docs(self):
        assert hyde.DEFAULT_QWEN_MODEL == "qwen3-coder-flash"
        option = next(o for o in config.OPTIONS if o.key == "HYDE_QWEN_MODEL")
        assert option.default == hyde.DEFAULT_QWEN_MODEL
        assert hyde.DEFAULT_QWEN_MODEL in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    def test_keys_are_read_per_provider_and_never_shared(self):
        assert hyde.PROVIDER_KEY_ENV == {
            "qwen": "QWEN_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }


class TestCriterion7TheCIguardsMatchTheLayout:
    def test_ci_checks_the_generated_trees_against_the_canonical_source(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "scripts/build_plugin_targets.py --check" in ci

    def test_the_plugin_version_guard_watches_the_generated_trees(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        watched = re.search(r"git diff [^\n]*-- skills/ agents/ commands/ hooks/ \.opencode/ \.qwen/", ci)
        assert watched, "the guard must watch the generated trees, not only the canonical source"
        assert "^+version = " in ci, "the guard must check the version in pyproject.toml"

    def test_lint_ignores_the_generated_trees(self):
        # The generated copies are checked by the generator, not by a formatter, so a formatter
        # rewriting them would make the parity guard fail on files nobody edited.
        assert {".opencode", ".qwen"} <= set(pyproject()["tool"]["ruff"]["extend-exclude"])

    def test_an_install_does_not_ship_the_generated_trees(self):
        manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        assert not re.search(r"^include .*(\.opencode|\.qwen)", manifest, re.MULTILINE)


def test_the_plan_and_the_tests_agree_on_which_criteria_need_a_live_client():
    plan = (REPO_ROOT / "docs" / "QWEN_ADAPTATION_PLAN.md").read_text(encoding="utf-8")
    section = plan.split("## 6. Acceptance criteria")[1].split("\n## ")[0]
    # A criterion no test can reach has to say so, otherwise a green suite reads as if it covered them.
    manual = [line for line in section.splitlines() if "manual" in line]
    assert len(manual) >= 2, section
