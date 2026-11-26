"""The generated per-client plugin trees (plan §4).

The canonical ``agents/``, ``commands/`` and ``skills/`` at the repository root are the
single source of truth, and ``scripts/build_plugin_targets.py`` writes a committed copy
for each client in that client's own vocabulary. Because the copies are committed rather
than built at run time, these tests are what keeps them honest: they must match the
generator byte for byte, carry no Claude-only frontmatter, and use each client's tool
names, argument syntax and hook contract.
"""

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_server import registration as reg

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "build_plugin_targets", REPO_ROOT / "scripts" / "build_plugin_targets.py"
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

TARGETS = (".opencode", ".qwen")
INVENTORY = {"agents": 13, "commands": 15, "skills": 17}

# Fields a canonical file may carry. Anything client-specific (Claude's `model:` or
# `allowed-tools`) has to be dropped, because the generator has to render the same
# source for two different vocabularies.
CANONICAL_FIELDS = {"name", "description", "argument-hint", "tools"}
NEUTRAL_TOOLS = set(gen.OPENCODE_TOOLS) | set(gen.QWEN_TOOLS)

MCP_TOOL_DECORATOR = re.compile(r"@mcp\.tool\(\)\s*(?:@[^\n]+\n\s*)*(?:async\s+)?def (\w+)")


def mcp_tool_names() -> set[str]:
    """Tool names the server actually exposes, so `tools:` entries can be checked against them."""
    names = set()
    for path in (REPO_ROOT / "mcp_server").rglob("*.py"):
        names.update(MCP_TOOL_DECORATOR.findall(path.read_text(encoding="utf-8")))
    return names


def canonical_files() -> dict[str, Path]:
    files = {}
    for group in ("agents", "commands"):
        for path in sorted((REPO_ROOT / group).glob("*.md")):
            files[f"{group}/{path.name}"] = path
    for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
        files[f"skills/{path.parent.name}/SKILL.md"] = path
    return files


def generated(target: str, relative: str) -> str:
    return (REPO_ROOT / target / relative).read_text(encoding="utf-8")


class TestTreesMatchTheGenerator:
    def test_committed_trees_are_current(self):
        # The CI job runs the same check, so a hand-edited .opencode/ or .qwen/ file fails here too.
        assert gen.main(["--check"]) == 0

    def test_generator_is_byte_stable(self):
        assert gen.build() == gen.build()

    def test_output_stays_inside_the_generated_trees(self):
        for path in gen.build():
            assert path.relative_to(REPO_ROOT).parts[0] in TARGETS

    def test_inventory(self):
        counts = {
            "agents": len(list((REPO_ROOT / "agents").glob("*.md"))),
            "commands": len(list((REPO_ROOT / "commands").glob("*.md"))),
            "skills": len(list((REPO_ROOT / "skills").glob("*/SKILL.md"))),
        }
        assert counts == INVENTORY
        for target in TARGETS:
            for group, expected in INVENTORY.items():
                written = [p for p in gen.build() if p.relative_to(REPO_ROOT).parts[:2] == (target, group)]
                if group == "skills":
                    # Skills also carry supporting files, so count the SKILL.md that defines each one.
                    written = [p for p in written if p.name == "SKILL.md"]
                assert len(written) == expected, f"{target}/{group}"


class TestCanonicalSourceIsClientNeutral:
    def test_only_portable_fields(self):
        for name, path in canonical_files().items():
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert set(fields) <= CANONICAL_FIELDS, name

    def test_no_claude_only_fields_anywhere(self):
        for name, path in canonical_files().items():
            text = path.read_text(encoding="utf-8")
            assert "allowed-tools:" not in text, name
            assert "\nmodel:" not in text, name

    def test_an_agent_that_uses_a_skill_lists_the_skill_tool(self):
        """An allowlist is a grant, so an agent told to invoke a skill has to name that tool.

        Agents with no `tools:` are unrestricted on both clients and need no grant; the ones
        that list tools are restricted to what they list, so a skill they mention has to be
        in the list or the instruction in the body cannot be carried out.
        """
        offenders = []
        for path in sorted((REPO_ROOT / "agents").glob("*.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            tokens = gen.neutral_tools(fields.get("tools", ""))
            if not tokens:
                continue
            if re.search(r"(uses|invokes|load)\S*[^\n]*skill", path.read_text(encoding="utf-8"), re.I) and (
                "skill" not in tokens
            ):
                offenders.append(path.name)
        assert offenders == []
        assert any("skill" in gen.neutral_tools(gen.parse_frontmatter(p.read_text(encoding="utf-8"))[0].get("tools", ""))
                   for p in (REPO_ROOT / "agents").glob("*.md"))

    def test_skill_tool_appears_in_both_trees_for_the_agents_that_list_it(self):
        checked = 0
        for path in sorted((REPO_ROOT / "agents").glob("*.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            if "skill" not in gen.neutral_tools(fields.get("tools", "")):
                continue
            checked += 1
            opencode = generated(".opencode", f"agents/{path.name}").split("---")[1]
            qwen = generated(".qwen", f"agents/{path.name}").split("---")[1]
            assert f"  - action: {gen.opencode_action('skill')}" in opencode, path.name
            assert f"  - {gen.qwen_tool_name('skill')}" in qwen, path.name
        assert checked > 0

    def test_tool_entries_are_neutral_or_real_mcp_tools(self):
        tools = mcp_tool_names()
        for name, path in canonical_files().items():
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            for token in gen.neutral_tools(fields.get("tools", "")):
                assert token in NEUTRAL_TOOLS or token in tools, f"{name}: {token}"

    def test_skill_ids_match_their_directories(self):
        # A skill ID comes from the directory name, so the frontmatter name must agree with it.
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert fields.get("name") == path.parent.name, path
            assert re.fullmatch(r"[a-z0-9_-]+", path.parent.name), path

    def test_every_skill_has_a_description(self):
        # Without a description the skill is not advertised to the client.
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert fields.get("description"), path

    def test_every_command_has_a_description(self):
        for path in sorted((REPO_ROOT / "commands").glob("*.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert fields.get("description"), path


class TestGeneratedFrontmatterShape:
    def test_no_field_is_written_empty(self):
        """A field name with nothing under it is not frontmatter a client can read.

        The generator leaves out an empty tool list instead of writing `tools:` on its
        own, so an agent that names no tools gets no tool field at all.
        """
        offenders = []
        for target in TARGETS:
            for path in sorted((REPO_ROOT / target).rglob("*.md")):
                lines = path.read_text(encoding="utf-8").split("\n")
                if lines[0] != "---":
                    continue
                block = lines[1 : lines.index("---", 1)]
                for index, line in enumerate(block):
                    if re.fullmatch(r"(tools|allowedTools|permissions):", line) and (
                        index + 1 >= len(block) or not block[index + 1].startswith("  - ")
                    ):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {line}")
        assert offenders == []

    def test_an_empty_tool_list_produces_no_field(self):
        source = {"name": "an-agent", "description": "Description.", "tools": ""}
        assert "permissions" not in gen.opencode_agent(source, "Body")
        assert "tools" not in gen.qwen_agent(source, "Body", [])
        assert "allowedTools" not in gen.qwen_skill(source, "Body", [])
        assert gen.render_frontmatter({"name": "a", "description": "d", "tools": []}) == "---\nname: a\ndescription: d\n---\n"

    def test_a_value_yaml_would_read_as_something_else_is_quoted(self):
        """Qwen Code validates `argument-hint` as a string, so `[test-type]` has to be quoted.

        Unquoted, YAML parses it as a flow sequence and the command file fails schema
        validation, so the whole command is skipped rather than loaded.
        """
        assert gen.yaml_string("[test-type]") == '"[test-type]"'
        assert gen.yaml_string("plain text") == "plain text"
        fields, _ = gen.parse_frontmatter(
            "---\ndescription: A command\nargument-hint: [test-type]\n---\nBody.\n"
        )
        rendered = gen.render_frontmatter({"description": fields["description"],
                                          "argument-hint": fields["argument-hint"]})
        assert 'argument-hint: "[test-type]"' in rendered
        # A value already written as a YAML string is left as it was, so the tree does not
        # grow quotes it does not need.
        assert gen.yaml_string("already quoted") == "already quoted"

    def test_no_generated_value_is_left_in_a_form_yaml_would_misread(self):
        # The canonical source does have a bracketed hint, so the check proves the generator
        # quotes it rather than passing the ambiguous form through.
        assert any(
            gen.yaml_string(value) != value
            for path in canonical_files().values()
            for value in gen.parse_frontmatter(path.read_text(encoding="utf-8"))[0].values()
            if isinstance(value, str)
        )
        offenders = []
        for target in TARGETS:
            for path in sorted((REPO_ROOT / target).rglob("*.md")):
                lines = path.read_text(encoding="utf-8").split("\n")
                if lines[0] != "---":
                    continue
                for line in lines[1 : lines.index("---", 1)]:
                    if line != line.strip():
                        continue  # a nested list item is not a top-level value
                    key, _, value = line.partition(":")
                    value = value.strip()
                    if value and value[0] in "?|>@`!&*%#[{'&" and not value.startswith('"'):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {key}")
        assert offenders == []

    def test_no_plugin_root_variable(self):
        # `CLAUDE_PLUGIN_ROOT` only exists inside the Claude plugin runtime; skills must use
        # MCP tools or paths relative to themselves instead.
        offenders = [
            path
            for path in (REPO_ROOT / t for t in (".opencode", ".qwen", "skills", "agents", "commands", "hooks"))
            for p in path.rglob("*")
            if p.is_file() and "CLAUDE_PLUGIN_ROOT" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        assert offenders == []


class TestToolMappingPerClient:
    def test_server_name_matches_registration(self):
        # Permission rules name the MCP server the setup command writes.
        assert gen.SERVER_NAME == reg.SERVER_NAME

    def test_opencode_agents_use_permission_rules(self):
        checked = 0
        for path in sorted((REPO_ROOT / "agents").glob("*.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            tokens = gen.neutral_tools(fields.get("tools", ""))
            text = generated(".opencode", f"agents/{path.name}")
            if not tokens:
                assert "permissions:" not in text, path.name
                continue
            checked += 1
            # OpenCode appends the rules to a policy that allows everything, so an allowlist has to
            # start by denying the default.
            assert '  - action: "*"' in text, path.name
            for token in tokens:
                action = gen.opencode_action(token)
                if action is None:
                    continue  # OpenCode has no permission action for this tool
                assert f"  - action: {action}" in text, f"{path.name}: {token}"
        assert checked > 0

    def test_the_env_ask_rule_survives_the_allow_block(self):
        # The base policy asks before reading `.env`; a deny-all allowlist would replace
        # that with a deny. Because the last match wins, the ask rule has to come after
        # the allow rules, not before them. Only an agent that names tools gets a policy,
        # so the check runs over the agents that do.
        checked = 0
        offenders = []
        for path in sorted((REPO_ROOT / "agents").glob("*.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            if not gen.neutral_tools(fields.get("tools", "")):
                continue
            checked += 1
            block = generated(".opencode", f"agents/{path.name}").split("---")[1]
            if '"*.env*"' not in block:
                offenders.append(f"{path.name}: no ask rule")
            elif block.rindex("effect: allow") > block.index('"*.env*"'):
                offenders.append(path.name)
        assert offenders == []
        assert checked > 0

    def test_mcp_tools_are_qualified_with_the_server_name(self):
        # A canonical `tools:` entry that is not a built-in is an MCP tool, and neither client
        # matches a bare tool name: Qwen wants `mcp__<server>__<tool>`, OpenCode wants the
        # permission action `<server>_<tool>`. OpenCode's sanitiser folds only characters outside
        # [A-Za-z0-9_-], so the server name in the action is the name setup registers.
        tool = "review_patent_claims"
        assert gen.is_mcp_tool(tool)
        assert gen.qwen_tool_name(tool) == f"mcp__{gen.SERVER_NAME}__{tool}"
        assert gen.opencode_action(tool) == f"{gen.SERVER_NAME}_{tool}"
        assert gen.SERVER_NAME == reg.SERVER_NAME

        # Where a client does read a tool list, the qualified name is what appears.
        source = {"name": "an-agent", "description": "Description.", "tools": tool}
        names = gen.mcp_tool_names(REPO_ROOT)
        assert len(names) == len(mcp_tool_names()), names
        assert tool in names
        assert f"  - mcp__{gen.SERVER_NAME}__{tool}" in gen.qwen_agent(source, "Body", names)
        assert f"  - action: {gen.SERVER_NAME}_*" in gen.opencode_agent(source, "Body")

    def test_both_trees_grant_the_same_tools_to_the_plugin_server(self):
        """The two trees have to allow the same set, expressed the way each client can.

        Qwen Code names tools one at a time; OpenCode matches an action of the form
        `<server>_<tool>`, so one group rule covers the same set. A list typed into the
        generator would drift from the server it is supposed to describe, so both come from
        the source that registers the tools.
        """
        names = gen.mcp_tool_names(REPO_ROOT)
        assert set(names) == mcp_tool_names()
        source = {"name": "an-agent", "description": "Description.", "tools": "read"}
        qwen = gen.qwen_agent(source, "Body", names)
        opencode = gen.opencode_agent(source, "Body")
        assert f"  - action: {gen.SERVER_NAME}_*" in opencode
        for name in names:
            assert f"mcp__{gen.SERVER_NAME}__{name}" in qwen
            assert f"{gen.SERVER_NAME}_{name}" == gen.opencode_action(name)
        # An agent that names no tools gets no list on either client, so it is left unrestricted.
        assert "mcp__" not in gen.qwen_agent({"name": "a", "description": "d"}, "Body", names)

    def test_command_tool_lists_are_dropped_because_neither_client_enforces_them(self):
        # Qwen Code's command schema keeps description, argument-hint, when_to_use and
        # disable-model-invocation and discards `allowedTools`; OpenCode has no command
        # allowlist at all. Writing one would look like an enforced permission and not be one,
        # so command tools are enforced through `permissions` in settings.json instead.
        source = {"description": "Description.", "tools": "review_patent_claims"}
        assert "tools" not in gen.command_fields(source, None)
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for target in TARGETS
            for path in (REPO_ROOT / target / "commands").rglob("*.md")
            if "allowedTools:" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_every_canonical_tool_has_a_mapping_for_at_least_one_client(self):
        # A tool the generator cannot name for a client must be dropped there, not written with the
        # other client's spelling.
        seen = 0
        for name, path in canonical_files().items():
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            for token in gen.neutral_tools(fields.get("tools", "")):
                seen += 1
                assert gen.qwen_tool_name(token), f"{name}: {token}"
                action = gen.opencode_action(token)
                if action is None:
                    assert token in gen.OPENCODE_UNMAPPED, f"{name}: {token}"
        assert seen > 0

    def test_qwen_agents_use_qwen_tool_names(self):
        text = generated(".qwen", "agents/patent-development.md")
        for name in ("read_file", "write_file", "edit", "glob", "grep_search", "run_shell_command"):
            assert f"  - {name}" in text, name
        assert "subagent:" not in text

    def test_open_code_skill_frontmatter_has_no_tool_list(self):
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            text = generated(".opencode", f"skills/{path.parent.name}/SKILL.md")
            assert "allowedTools" not in text, path.name
            if not fields.get("tools"):
                assert "allowed-tools" not in text, path.name

    def test_qwen_skills_carry_allowed_tools_when_canonical_has_them(self):
        seen = 0
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            text = generated(".qwen", f"skills/{path.parent.name}/SKILL.md")
            tokens = gen.neutral_tools(fields.get("tools", ""))
            if tokens:
                seen += 1
                assert "allowedTools:" in text, path.name
                for token in tokens:
                    assert f"  - {gen.qwen_tool_name(token)}" in text, f"{path.name}: {token}"
            else:
                assert "allowedTools:" not in text, path.name
        assert seen > 0

    def test_prose_tool_names_are_mapped_per_client(self):
        # Only backticked single tokens are rewritten, so a mapping must not leak across targets.
        hits = 0
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            body = gen.parse_frontmatter(path.read_text(encoding="utf-8"))[1]
            if "`websearch`" not in body:
                continue
            hits += 1
            assert "`websearch`" in generated(".opencode", f"skills/{path.parent.name}/SKILL.md")
            assert "`web_search`" in generated(".qwen", f"skills/{path.parent.name}/SKILL.md")
        assert hits > 0


class TestArgumentSyntax:
    def test_commands_use_each_clients_argument_placeholder(self):
        checked = 0
        for path in sorted((REPO_ROOT / "commands").glob("*.md")):
            body = gen.parse_frontmatter(path.read_text(encoding="utf-8"))[1]
            if "$ARGUMENTS" not in body:
                continue
            checked += 1
            assert "$ARGUMENTS" in generated(".opencode", f"commands/{path.name}"), path.name
            qwen = generated(".qwen", f"commands/{path.name}")
            assert "{{args}}" in qwen, path.name
            assert "$ARGUMENTS" not in qwen, path.name
        assert checked > 0


class TestHooks:
    def test_hooks_are_ported_to_qwen_settings_only(self):
        settings = json.loads(generated(".qwen", "settings.json"))
        canonical = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        assert set(settings["hooks"]) == set(canonical["hooks"])
        assert not (REPO_ROOT / ".opencode" / "settings.json").exists()
        assert not any("hooks" in p.relative_to(REPO_ROOT).parts for p in gen.build())

    def test_bash_matcher_becomes_qwens_shell_tool(self):
        settings = json.loads(generated(".qwen", "settings.json"))
        entry = settings["hooks"]["PreToolUse"][0]
        # A Qwen matcher is matched against the tool name only, so Claude's
        # `Bash.*python.*mcp_server` cannot port as a pattern: the tool it names is
        # kept and the qualifier is dropped rather than shipping a matcher that never fires.
        assert entry["matcher"] == "run_shell_command"
        assert entry["hooks"][0]["type"] == "command"

    def test_session_start_text_survives(self):
        settings = json.loads(generated(".qwen", "settings.json"))
        command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "Patent Creator System Ready" in command

    def test_the_session_script_does_not_depend_on_one_clients_env_file(self, tmp_path):
        """`$CLAUDE_ENV_FILE` only exists in the Claude hook runtime.

        OpenCode and Qwen Code read what a hook prints, so the script prints its notes as well
        as appending them, and it still reports when a client hands it no file at all.
        """
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash is needed to run the session-start script")

        credentials = tmp_path / ".config" / "gcloud"
        credentials.mkdir(parents=True)
        (credentials / "application_default_credentials.json").write_text("{}", encoding="utf-8")
        script = str(REPO_ROOT / "scripts" / "setup-env.sh")

        env_file = tmp_path / "claude-env.txt"
        run = subprocess.run(
            [bash, script],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path), "CLAUDE_ENV_FILE": str(env_file)},
        )
        assert run.returncode == 0, run.stderr
        assert "# BigQuery authenticated" in run.stdout
        assert "# BigQuery authenticated" in env_file.read_text(encoding="utf-8")

        # The same script under a client that gives it nothing: the notes still reach the run.
        plain = subprocess.run(
            [bash, script],
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"} | {"HOME": str(tmp_path)},
        )
        assert plain.returncode == 0, plain.stderr
        assert "# BigQuery authenticated" in plain.stdout
        assert sorted(p.name for p in tmp_path.iterdir()) == [".config", "claude-env.txt"]


class TestSupportingFiles:
    def test_skill_supporting_files_are_copied_verbatim(self):
        for path in sorted(p for p in (REPO_ROOT / "skills").rglob("*") if p.is_file() and p.name != "SKILL.md"):
            if "__pycache__" in path.parts:
                continue
            for target in TARGETS:
                copy = REPO_ROOT / target / "skills" / path.parent.name / path.name
                assert copy.read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), copy

    def test_python_cache_is_not_generated(self):
        assert not any("__pycache__" in p.parts for p in gen.build())


class TestCanonicalScriptsAreIdempotent:
    """The two scripts that keep the canonical source neutral must not want to change it again.

    They are run by hand, so the only guard is that a re-run finds nothing to rewrite: a file the
    scripts would change means the committed source is not in the form the generator assumes.
    """

    def _load(self, name: str):
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_normaliser_leaves_the_canonical_source_unchanged(self):
        norm = self._load("normalise_canonical")
        for name, path in canonical_files().items():
            assert norm.normalise(path.read_text(encoding="utf-8")) == path.read_text(encoding="utf-8"), name

    def test_renamer_leaves_the_instruction_files_unchanged(self):
        renamer = self._load("rename_command_refs")
        for entry in renamer.TARGETS:
            path = REPO_ROOT / entry
            files = (
                [p for p in sorted(path.rglob("*.md")) if "__pycache__" not in p.parts]
                if path.is_dir()
                else [path]
            )
            for file in files:
                if not file.is_file() or renamer.is_excluded(file, REPO_ROOT):
                    continue
                text = file.read_text(encoding="utf-8")
                assert renamer.rewrite(text) == text, file.relative_to(REPO_ROOT)

    def test_renamer_targets_exist(self):
        for entry in (REPO_ROOT / e for e in self._load("rename_command_refs").TARGETS):
            assert entry.exists(), entry

    def test_renamer_refuses_to_run_outside_the_checkout_root(self, monkeypatch, tmp_path, capsys):
        # The targets are resolved from ROOT, so a run from another directory would write
        # wherever the reader happened to be standing.
        renamer = self._load("rename_command_refs")
        monkeypatch.setattr(renamer, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path.parent)
        assert renamer.main() == 1
        assert "checkout root" in capsys.readouterr().err
        assert not any(tmp_path.iterdir())

    def test_renamer_keeps_a_crlf_document_as_crlf(self, monkeypatch, tmp_path):
        renamer = self._load("rename_command_refs")
        monkeypatch.setattr(renamer, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_bytes(b"Run patent-creator setup\r\nin patent-creator-venv\r\n")
        assert renamer.main() == 0
        assert readme.read_bytes() == (
            b"Run qwen-patent-creator setup\r\nin qwen-patent-creator-venv\r\n"
        )

    def test_renamer_matches_a_single_windows_separator(self):
        # A documented Windows path is `Scripts\patent-creator.exe`, one backslash. A pattern
        # that needs two would leave those lines naming the upstream command.
        renamer = self._load("rename_command_refs")
        assert renamer.rewrite(r"Scripts\patent-creator.exe") == r"Scripts\qwen-patent-creator.exe"
        assert renamer.rewrite(r"patent-creator-venv\Scripts\patent-creator.exe") == (
            r"qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe"
        )
        assert renamer.rewrite("bin/patent-creator --version") == "bin/qwen-patent-creator --version"

    def test_exclusion_recognises_a_path_as_either_platform_prints_it(self):
        renamer = self._load("rename_command_refs")
        windows = pathlib.PureWindowsPath("C:/checkout")
        assert renamer.is_excluded(windows / "docs" / "QWEN_ADAPTATION_PLAN.md", windows)
        assert not renamer.is_excluded(windows / "docs" / "OTHER.md", windows)

    def test_renamer_leaves_the_excluded_record_alone(self, monkeypatch, tmp_path):
        renamer = self._load("rename_command_refs")
        monkeypatch.setattr(renamer, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "docs" / "QWEN_ADAPTATION_PLAN.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("The fork was `patent-creator setup` at the fork point.\n", encoding="utf-8")
        readme = tmp_path / "README.md"
        readme.write_text("Run patent-creator setup.\n", encoding="utf-8")
        assert renamer.main() == 0
        assert "patent-creator setup" in plan.read_text(encoding="utf-8")
        assert "qwen-patent-creator setup" in readme.read_text(encoding="utf-8")


def minimal_canonical_tree(root: Path) -> Path:
    """A canonical source small enough to run the generator against in a temp directory.

    The generator reads the three canonical groups plus `hooks/hooks.json` and writes only
    into the two target trees, so this is the whole input it needs; the tests below can run
    it against a copy without touching the repository.
    """
    for group in ("agents", "commands", "skills", "hooks"):
        (root / group).mkdir(parents=True, exist_ok=True)
    (root / "agents" / "an-agent.md").write_text(
        "---\nname: an-agent\ndescription: Does a thing.\ntools: read, bash\n---\nBody.\n",
        encoding="utf-8",
    )
    (root / "commands" / "a-command.md").write_text(
        "---\ndescription: Runs with arguments.\n---\nUses $ARGUMENTS.\n", encoding="utf-8"
    )
    skill = root / "skills" / "a-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: A skill.\ntools: grep\n---\nBody.\n", encoding="utf-8"
    )
    (root / "hooks" / "hooks.json").write_text('{"hooks": {}}\n', encoding="utf-8")
    return root


class TestGeneratorRoundTripsTheBytesItWrites:
    """The committed trees are compared as bytes, so reading one back has to give what was written.

    UTF-8 is explicit throughout the generator because a locale-dependent decode would make the
    same committed file look different on another machine, and the check compares content strings
    against what is on disk.
    """

    def test_committed_files_read_back_byte_for_byte(self):
        offenders = []
        for path, content in gen.build().items():
            with path.open(encoding="utf-8", newline="") as handle:
                if handle.read() != content:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_a_crlf_supporting_file_is_copied_as_the_bytes_it_read(self, tmp_path, monkeypatch):
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        source = root / "skills" / "a-skill" / "notes.txt"
        source.write_bytes(b"first line\r\nsecond line\r\n")
        gen.main([])
        for target in TARGETS:
            copy = root / target / "skills" / "a-skill" / "notes.txt"
            assert copy.read_bytes() == b"first line\r\nsecond line\r\n", target

    def test_stale_generated_files_are_removed(self, tmp_path, monkeypatch):
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        stray = root / ".opencode" / "skills" / "a-skill" / "left-over.md"
        stray.write_text("nothing generates this\n", encoding="utf-8")
        assert gen.main([]) == 0
        assert not stray.exists()

    def test_check_reports_a_stale_generated_file_without_removing_it(self, tmp_path, monkeypatch):
        """`--check` is a verification mode, so it reports stale files instead of cleaning them up."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        stray = root / ".qwen" / "commands" / "left-over.md"
        stray.write_text("nothing generates this\n", encoding="utf-8")
        assert gen.main(["--check"]) == 1
        assert stray.exists(), "the file is reported, not deleted"
        assert gen.main([]) == 0
        assert not stray.exists()
        assert gen.main(["--check"]) == 0

    def test_check_reports_a_hand_edited_generated_file(self, tmp_path, monkeypatch):
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        hand_edited = root / ".qwen" / "agents" / "an-agent.md"
        hand_edited.write_text(hand_edited.read_text(encoding="utf-8") + "\nEdited by hand.\n", encoding="utf-8")
        assert gen.main(["--check"]) == 1
        assert gen.main([]) == 0
        assert gen.main(["--check"]) == 0

    def test_check_reports_a_file_that_is_not_the_bytes_the_generator_writes(self, tmp_path, monkeypatch):
        """A committed CRLF file is not 'the same content' as the LF content in the tree."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        drifted = root / ".opencode" / "agents" / "an-agent.md"
        drifted.write_bytes(drifted.read_bytes().replace(b"\n", b"\r\n"))
        assert gen.main(["--check"]) == 1
        assert gen.main([]) == 0
        assert drifted.read_bytes() == (root / ".opencode" / "agents" / "an-agent.md").read_bytes()

    def test_build_writes_against_the_root_it_is_given(self, tmp_path, monkeypatch):
        """--root and build(root) are what let a test run the generator against a fixture."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.chdir(tmp_path.parent)
        for path, content in gen.build(root).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        assert gen.main(["--check", "--root", str(root)]) == 0
        offenders = [
            path for path in gen.build(root) if path.read_bytes() != gen.build(root)[path].encode("utf-8")
        ]
        assert offenders == []
        assert all(path.relative_to(root).parts[0] in TARGETS for path in gen.build(root))

    def test_a_file_the_generator_did_not_write_is_left_alone(self, tmp_path, monkeypatch):
        """A generated tree is committed, so it can hold someone else's file; stale cleanup
        may only remove the shapes the generator itself writes."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        keep = [
            root / ".qwen" / "notes.md",
            root / ".opencode" / "agents" / "README.txt",
            root / ".opencode" / "skills" / "a-hand-added-skill" / "notes.md",
        ]
        for path in keep:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not generator output\n", encoding="utf-8")
        assert gen.main([]) == 0
        assert gen.main(["--check"]) == 0
        for path in keep:
            assert path.exists(), path
            assert path.read_text(encoding="utf-8") == "not generator output\n", path

    def test_a_markdown_file_in_a_mirrored_skill_directory_is_generator_output(self, tmp_path, monkeypatch):
        """A skill copy mirrors its canonical directory, so anything inside it comes from the source.

        That is what makes stale cleanup safe: the generator only removes a file whose path it
        would itself write, and it writes the whole mirror of each canonical skill directory.
        A directory the canonical source does not have is not something it would write, so a
        hand-added file there is left alone.
        """
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        mirrored = root / ".opencode" / "skills" / "a-skill" / "notes.md"
        orphan = root / ".opencode" / "skills" / "a-hand-added-skill" / "notes.md"
        for path in (mirrored, orphan):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("text\n", encoding="utf-8")
        assert gen.is_generator_owned(mirrored, root)
        assert not gen.is_generator_owned(orphan, root)
        assert gen.main([]) == 0
        assert not mirrored.exists()
        assert orphan.exists()

    def test_an_existing_settings_json_is_never_rewritten(self, tmp_path, monkeypatch, capsys):
        """Qwen Code reads .qwen/settings.json as user configuration, so writing never overwrites it."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        fresh = root / ".qwen" / "settings.json"
        fresh.parent.mkdir(parents=True, exist_ok=True)
        fresh.write_text('{ "hooks": { "PostToolUse": [] } }\n', encoding="utf-8")
        assert gen.main([]) == 0
        assert fresh.read_text(encoding="utf-8") == '{ "hooks": { "PostToolUse": [] } }\n'
        assert gen.is_generator_owned(fresh, root) and gen.create_only(fresh, root)
        # `--check` is a verification mode rather than a write, so it still compares that file:
        # the committed settings.json is the hooks source rendered, and without the comparison a
        # change to hooks/hooks.json would leave `--check` passing on a tree that no longer matches it.
        assert gen.main(["--check"]) == 1, "the committed settings.json is checked against the hooks source"
        # Because writing skips an existing settings.json, regenerating cannot resolve that drift, so
        # the message says what to do about it rather than telling the developer to run the generator.
        assert "user configuration" in capsys.readouterr().out

    def test_a_change_to_the_hooks_source_is_visible_to_check(self, tmp_path, monkeypatch):
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        assert gen.main([]) == 0
        assert gen.main(["--check"]) == 0
        (root / "hooks" / "hooks.json").write_text(
            '{"hooks": {"SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo hi"}]}]}}\n',
            encoding="utf-8",
        )
        assert gen.main(["--check"]) == 1
        # The existing settings.json is not rewritten, so this is what a developer has to do:
        # remove the file (or move their own hooks into the canonical source) and regenerate.
        assert gen.main([]) == 0
        assert gen.main(["--check"]) == 1
        (root / ".qwen" / "settings.json").unlink()
        assert gen.main([]) == 0
        assert gen.main(["--check"]) == 0
        assert "SessionStart" in (root / ".qwen" / "settings.json").read_text(encoding="utf-8")
        # OpenCode has no hook mechanism equivalent to Qwen's settings.json, so only the Qwen tree
        # carries the hooks and only that file can be out of date with the source.
        assert not (root / ".opencode" / "settings.json").exists()

    def test_a_skill_name_qwen_cannot_match_is_skipped_for_that_target(self, tmp_path, monkeypatch):
        """A name Qwen's pattern refuses would add a skill to the tree that the client never loads."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        skill = root / "skills" / "a-skill"
        (skill / "SKILL.md").write_text(
            "---\nname: a skill\ndescription: A skill with a space in its name.\n---\nBody.\n",
            encoding="utf-8",
        )
        generated = gen.build(root)
        assert root / ".opencode" / "skills" / "a-skill" / "SKILL.md" in generated
        assert root / ".qwen" / "skills" / "a-skill" / "SKILL.md" not in generated
        assert gen.main([]) == 0
        assert (root / ".opencode" / "skills" / "a-skill" / "SKILL.md").exists()
        assert not (root / ".qwen" / "skills" / "a-skill" / "SKILL.md").exists()
        assert gen.main(["--check"]) == 0

    def test_stale_cleanup_removes_only_generator_output(self, tmp_path, monkeypatch):
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        gen.main([])
        removed = root / ".qwen" / "agents" / "gone.md"
        removed.write_text("was generator output\n", encoding="utf-8")
        assert gen.is_generator_owned(removed, root)
        assert not gen.is_generator_owned(root / ".qwen" / "notes.md", root)
        assert gen.main([]) == 0
        assert not removed.exists()
        assert gen.main(["--check"]) == 0

    def test_nested_command_and_skill_paths_survive_the_copy(self, tmp_path, monkeypatch):
        """`commands/team/review.md` is the command /team/review, and a skill resolves its
        support files relative to the skill directory, so both keep their subpaths."""
        root = minimal_canonical_tree(tmp_path)
        monkeypatch.setattr(gen, "ROOT", root)
        nested = root / "commands" / "team"
        nested.mkdir(parents=True)
        (nested / "review.md").write_text(
            "---\ndescription: Reviews a claim chart.\n---\nUses $ARGUMENTS.\n", encoding="utf-8"
        )
        support = root / "skills" / "a-skill" / "references"
        support.mkdir(parents=True)
        (support / "checklist.md").write_text("Checklist.\n", encoding="utf-8")
        generated = gen.build(root)
        for target in TARGETS:
            assert (root / target / "commands" / "team" / "review.md") in generated, target
            assert (root / target / "skills" / "a-skill" / "references" / "checklist.md") in generated, target


class TestSkillNamesFitWhatTheClientsAccept:
    """Each client has its own rules for a skill name, and the generator inherits the directory name."""

    # Qwen Code matches a skill name against letters, numbers and `_ : . -`; OpenCode builds the
    # skill ID from the path and keeps it lowercase with `-` and `_`.
    QWEN_SKILL_NAME = re.compile(r"[A-Za-z0-9_:.-]+\Z")
    OPENCODE_SKILL_ID = re.compile(r"[a-z0-9_-]+\Z")

    def test_skill_directory_names_fit_both_clients(self):
        offenders = []
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            name = path.parent.name
            if not self.QWEN_SKILL_NAME.fullmatch(name):
                offenders.append(f"qwen: {name}")
            if not self.OPENCODE_SKILL_ID.fullmatch(name):
                offenders.append(f"opencode: {name}")
        assert offenders == []

    def test_qwen_skill_name_matches_its_directory(self):
        offenders = []
        for path in sorted((REPO_ROOT / "skills").glob("*/SKILL.md")):
            fields, _ = gen.parse_frontmatter(path.read_text(encoding="utf-8"))
            name = fields.get("name", "")
            if name and name != path.parent.name:
                offenders.append(f"{path.parent.name}: {name}")
        assert offenders == []


COMMAND_REFERENCE = re.compile(r"`/([a-z][a-z0-9-]{2,})`")

# Slash commands the client itself provides, mentioned in the instruction files but not
# defined by this repository. Kept empty on purpose: every command reference in the canonical
# source resolves to a command file, so nothing has to be excused today.
CLIENT_BUILTIN_COMMANDS: set[str] = set()


class TestCommandReferencesResolve:
    """A `/name` in an instruction file is a promise that the command exists."""

    def test_every_command_reference_names_a_real_command(self):
        offenders = []
        for group in ("agents", "commands", "skills"):
            for path in sorted((REPO_ROOT / group).rglob("*.md")):
                for name in COMMAND_REFERENCE.findall(path.read_text(encoding="utf-8")):
                    if name in CLIENT_BUILTIN_COMMANDS:
                        continue
                    if not (REPO_ROOT / "commands" / f"{name}.md").exists():
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: /{name}")
        assert offenders == []

    def test_no_command_file_is_missing_from_a_target_tree(self):
        offenders = []
        for path in sorted((REPO_ROOT / "commands").glob("*.md")):
            for target in TARGETS:
                copy = REPO_ROOT / target / "commands" / path.name
                if not copy.exists():
                    offenders.append(str(copy.relative_to(REPO_ROOT)))
        assert offenders == []
