#!/usr/bin/env python3
"""Generate the per-client plugin trees from the canonical plugin source.

The canonical ``agents/``, ``commands/`` and ``skills/`` trees at the repository root are the single
source of truth. Each MCP client reads its own directory layout and frontmatter vocabulary, so this
script writes committed copies:

- ``.opencode/`` - the primary target (OpenCode),
- ``.qwen/`` - the secondary target (Qwen Code CLI).

Skill supporting files (``*.py``, extra Markdown) are copied verbatim because paths inside a skill are
resolved relative to the skill directory. Output is byte-stable: running the script twice produces the
same files, and ``--check`` fails if the committed trees have drifted from the canonical source.

Per-target frontmatter mapping:

- agents: a canonical ``tools:`` allowlist becomes OpenCode ``permissions`` rules (deny-all first, then one
  allow rule per tool, because OpenCode's base policy allows everything) and a Qwen Code ``tools`` list.
  The deny-all would also block the MCP server these agents exist to call, so the plugin's own server is
  allowed as a group, and the base policy's ``ask`` for ``*.env`` is re-added after the allow block because
  the last matching rule wins.
- skills: Qwen Code reads ``allowedTools``; OpenCode has no tool list for a skill, so the field is dropped.
- commands: neither client enforces a command tool allowlist, so it is not written. Qwen Code's command
  loader reads only description, argument-hint, when_to_use and disable-model-invocation and discards the
  rest; OpenCode has no tool allowlist for a command at all.
- OpenCode has no permission action for a todo list, so ``todowrite`` cannot be expressed as a rule.

The canonical vocabulary says nothing about directories, so the generated OpenCode rules keep its default
allowance for paths outside the project when the agent may read or write.

Run from the repository root (or pass ``--root``), and use ``--check`` in CI.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must match mcp_server.registration.SERVER_NAME so permission rules name the same MCP server.
SERVER_NAME = "qwen-patent-creator"

# A canonical `tools:` entry that is not a neutral built-in is an MCP tool, so each client has to see it
# qualified with the server: Qwen Code matches MCP tools as `mcp__<server>__<tool>`, and OpenCode names an
# MCP tool's permission action `<server>_<tool>`. OpenCode sanitises both names by replacing characters
# outside [A-Za-z0-9_-] with `_`, which keeps the hyphen in the server name.
OPENCODE_SANITISE = re.compile(r"[^A-Za-z0-9_-]")

# Neutral frontmatter vocabulary -> client tool names.
OPENCODE_TOOLS = {
    "read": "read",
    "write": "edit",
    "edit": "edit",
    "glob": "glob",
    "grep": "grep",
    "bash": "shell",
    "webfetch": "webfetch",
    "websearch": "websearch",
    "subagent": "subagent",
    "skill": "skill",
}

QWEN_TOOLS = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit",
    "glob": "glob",
    "grep": "grep_search",
    "bash": "run_shell_command",
    "webfetch": "web_fetch",
    "websearch": "web_search",
    "subagent": "agent",
    "skill": "skill",
    "todowrite": "todo_write",
}

# OpenCode has no permission action for todo lists, so that tool cannot be expressed as a rule.
OPENCODE_UNMAPPED = {"todowrite"}

FRONTMATTER = re.compile(r"\A---\n(?P<head>.*?)(\n)?---\n", re.DOTALL)
BACKTICKED_TOOL = re.compile(r"`([A-Za-z_][A-Za-z_0-9]*)`")

EXCLUDED_PARTS = ("__pycache__",)

TARGETS = (".opencode", ".qwen")

# Qwen Code validates a skill name against /^[\p{L}\p{N}_:.-]+$/u when it loads the skill.
# Python's \w under Unicode covers \p{L}, \p{N} and the underscore, so this is the same set.
QWEN_SKILL_NAME = re.compile(r"^[\w:.-]+$")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return the frontmatter fields and the body. Canonical frontmatter is one ``key: value`` per line."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group("head").splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, text[match.end():]


def neutral_tools(value: str) -> list[str]:
    return [token.strip() for token in value.split(",") if token.strip()]


def is_mcp_tool(token: str) -> bool:
    return token not in OPENCODE_TOOLS and token not in QWEN_TOOLS


def mcp_tool_names(root: pathlib.Path) -> list[str]:
    """The tool names the MCP server registers, read from the source that defines them.

    Both trees have to grant the same set of MCP tools, so the names come from the server rather
    than from a list typed into the generator. Qwen Code can only name tools one at a time, so it
    needs them; OpenCode expresses the same set with one rule for the whole server.
    """
    names = []
    for path in sorted((root / "mcp_server" / "tools").rglob("*.py")):
        lines = read_text(path).splitlines()
        for index, line in enumerate(lines):
            if line.strip() != "@mcp.tool()":
                continue
            # The tool name is on the def line, after any decorator between it and the tool decorator.
            for later in lines[index + 1:index + 6]:
                match = re.match(r"\s*(?:async\s+)?def (\w+)\(", later)
                if match:
                    names.append(match.group(1))
                    break
    return names


def qwen_tool_name(token: str) -> str:
    """How Qwen Code matches a canonical tool. An MCP tool needs the server prefix to match at all."""
    if is_mcp_tool(token):
        return f"mcp__{SERVER_NAME}__{token}"
    return QWEN_TOOLS[token]


def opencode_action(token: str) -> str | None:
    """Permission action OpenCode matches for a canonical tool, or ``None`` when it has no action for it."""
    if is_mcp_tool(token):
        # OpenCode sanitises tool names the same way it sanitises the server name, so the server part of
        # the action is the name setup registers and the tool part keeps any name it can match exactly.
        return f"{SERVER_NAME}_{OPENCODE_SANITISE.sub('_', token)}"
    return OPENCODE_TOOLS.get(token)


def yaml_string(value: str) -> str:
    """Quote a frontmatter value that YAML would read as something other than a string.

    `[test-type]` is a flow sequence, and Qwen Code's command schema requires `argument-hint` to be a
    string, so an unquoted bracketed hint fails validation and the whole command file is skipped.
    """
    if (
        not value
        or value[0] in "?|>@`!&*%#[{'\"&"
        or value.startswith("- ")
        or ": " in value
        or value.endswith((":", " #"))
    ):
        return json.dumps(value)
    return value


def render_frontmatter(fields: dict[str, str | list]) -> str:
    """Render frontmatter in insertion order so output is byte-stable."""
    lines = []
    for key, value in fields.items():
        if not value:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    for index, (name, val) in enumerate(item.items()):
                        indent = "  - " if index == 0 else "    "
                        lines.append(f"{indent}{name}: {val}")
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {yaml_string(value)}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def map_prose(body: str, table: dict[str, str]) -> str:
    def replace(match):
        token = match.group(1)
        return f"`{table.get(token, token)}`"

    return BACKTICKED_TOOL.sub(replace, body)


def opencode_command_body(body: str) -> str:
    # OpenCode uses $ARGUMENTS, which is also the canonical form.
    return map_prose(body, OPENCODE_TOOLS)


def qwen_command_body(body: str) -> str:
    body = body.replace("$ARGUMENTS", "{{args}}").replace("$1", "{{args}}")
    return map_prose(body, QWEN_TOOLS)


def opencode_agent(fields: dict[str, str], body: str) -> str:
    out = {"name": fields.get("name", ""), "description": fields.get("description", ""), "mode": "subagent"}
    tokens = neutral_tools(fields.get("tools", ""))
    if not tokens:
        return render_frontmatter(out) + map_prose(body, OPENCODE_TOOLS)
    # OpenCode's base policy allows every action, so a canonical allowlist needs the deny-all first.
    rules = [{"action": '"*"', "resource": '"*"', "effect": "deny"}]
    if any(token in {"read", "write", "edit"} for token in tokens):
        # The canonical vocabulary has no directory action; keep OpenCode's default external-path allowance.
        rules.append({"action": "external_directory", "resource": '"*"', "effect": "allow"})
    for token in tokens:
        action = opencode_action(token)
        if action is None:
            continue  # no OpenCode permission action for this tool
        rule = {"action": action, "resource": '"*"', "effect": "allow"}
        if rule in rules:
            continue  # `write` and `edit` map to the same OpenCode action
        rules.append(rule)
    # The deny-all above would also block the MCP server these agents exist to call, so the plugin's own
    # server is allowed as a group: it is the same set of tools as the names the Qwen Code tree lists,
    # because Qwen Code cannot express a group and has to name them one by one. An agent that names
    # specific MCP tools gets those rules from the loop above as well.
    rules.append({"action": f"{SERVER_NAME}_*", "resource": '"*"', "effect": "allow"})
    out["permissions"] = rules
    if "read" in tokens:
        # OpenCode's base policy answers `read *.env` with `ask`; the deny-all above
        # would replace that with `deny`. Re-adding it after the allow block keeps the
        # prompt, because the last matching rule wins. The resource has to cover the whole
        # family (`path.env`, `.env.local`), not only the exact `*.env` shape.
        rules.append({"action": "read", "resource": '"*.env*"', "effect": "ask"})
    return render_frontmatter(out) + map_prose(body, OPENCODE_TOOLS)


def qwen_agent(fields: dict[str, str], body: str, mcp_names: list[str]) -> str:
    out = {"name": fields.get("name", ""), "description": fields.get("description", "")}
    tokens = neutral_tools(fields.get("tools", ""))
    if tokens:
        # Qwen Code matches an MCP tool only by its qualified name, so an agent that lists tools has to
        # get the server's tools named one by one: this is the same set OpenCode grants with one rule
        # for the whole server. An agent with no allowlist is left unrestricted on both clients.
        out["tools"] = [qwen_tool_name(token) for token in tokens] + [
            f"mcp__{SERVER_NAME}__{name}" for name in mcp_names
        ]
    return render_frontmatter(out) + map_prose(body, QWEN_TOOLS)


def opencode_skill(fields: dict[str, str], body: str) -> str:
    out = {"name": fields.get("name", ""), "description": fields.get("description", "")}
    for key in ("disable-model-invocation",):
        if key in fields:
            out[key] = fields[key]
    return render_frontmatter(out) + map_prose(body, OPENCODE_TOOLS)


def qwen_skill(fields: dict[str, str], body: str, mcp_names: list[str]) -> str:
    out = {"name": fields.get("name", ""), "description": fields.get("description", "")}
    tokens = neutral_tools(fields.get("tools", ""))
    if tokens:
        out["allowedTools"] = [qwen_tool_name(token) for token in tokens] + [
            f"mcp__{SERVER_NAME}__{name}" for name in mcp_names
        ]
    for key in ("priority", "paths", "user-invocable", "disable-model-invocation"):
        if key in fields:
            out[key] = fields[key]
    return render_frontmatter(out) + map_prose(body, QWEN_TOOLS)


def command_fields(fields: dict[str, str], tools_key: str | None) -> dict[str, str | list[str]]:
    out = {"description": fields.get("description", "")}
    if fields.get("argument-hint"):
        out["argument-hint"] = fields["argument-hint"]
    if tools_key:
        names = [qwen_tool_name(token) for token in neutral_tools(fields.get("tools", ""))]
        if names:
            out[tools_key] = names
    return out


def build_hook_settings(hook_file: pathlib.Path) -> dict:
    """Port hooks/hooks.json to Qwen Code settings.json hooks.

    OpenCode has no declarative hook configuration the way Claude and Qwen Code do:
    OpenCode hooks are code in a plugin, not a settings file, so there is nothing here
    to generate for it and the hooks are ported only to Qwen Code.
    """
    data = json.loads(read_text(hook_file))
    hooks = {}
    for event, entries in data.get("hooks", {}).items():
        ported = []
        for entry in entries:
            matcher = entry.get("matcher")
            out = {}
            if matcher:
                # A Qwen Code matcher is matched against the tool name only, so the
                # qualifier part of Claude's `Bash.*python.*mcp_server` can never fire.
                # Keep the tool it names and drop the qualifier rather than shipping a
                # pattern that silently does nothing.
                head = re.split(r"[.*]", matcher, maxsplit=1)[0]
                out["matcher"] = "run_shell_command" if head == "Bash" else matcher
            out["hooks"] = [
                {"type": "command", "command": hook.get("command", "")}
                for hook in entry.get("hooks", [])
            ]
            ported.append(out)
        hooks[event] = ported
    return {"hooks": hooks}


def read_text(path: pathlib.Path) -> str:
    """Read as UTF-8 always: the generated trees are committed, so a locale-dependent
    decode would make the same file drift on another machine."""
    return path.read_text(encoding="utf-8")


def read_preserving_newlines(path: pathlib.Path) -> str:
    """Read without turning CRLF into LF, so a Windows-authored file stays as it is."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def build(root: pathlib.Path = ROOT) -> dict[pathlib.Path, str]:
    files: dict[pathlib.Path, str] = {}
    mcp_names = mcp_tool_names(root)

    for path in sorted((root / "agents").glob("*.md")):
        fields, body = parse_frontmatter(read_text(path))
        stem = path.stem
        files[root / ".opencode" / "agents" / f"{stem}.md"] = opencode_agent(fields, body)
        files[root / ".qwen" / "agents" / f"{stem}.md"] = qwen_agent(fields, body, mcp_names)

    for path in sorted((root / "commands").rglob("*.md")):
        fields, body = parse_frontmatter(read_text(path))
        # A nested command directory is part of the command name: `commands/team/review.md` is the
        # command `/team/review`, so the generated path keeps the subdirectory.
        name = path.relative_to(root / "commands").with_suffix(".md").as_posix()
        files[root / ".opencode" / "commands" / name] = render_frontmatter(
            command_fields(fields, None)
        ) + opencode_command_body(body)
        # Qwen Code's command loader reads description, argument-hint, when_to_use and
        # disable-model-invocation and discards anything else, so a tool allowlist here would
        # look like an enforced permission and not be one. It is left out; Qwen Code enforces
        # command tools through `permissions` in settings.json.
        files[root / ".qwen" / "commands" / name] = render_frontmatter(
            command_fields(fields, None)
        ) + qwen_command_body(body)

    for path in sorted((root / "skills").glob("*/SKILL.md")):
        fields, body = parse_frontmatter(read_text(path))
        skill_dir = path.parent.name
        files[root / ".opencode" / "skills" / skill_dir / "SKILL.md"] = opencode_skill(fields, body)
        name = fields.get("name", "")
        if not QWEN_SKILL_NAME.match(name):
            # Qwen Code refuses a skill whose name its pattern cannot match, so writing the file
            # would add a skill to the tree that the client never loads. tests report the
            # offending names rather than the generator shipping them.
            continue
        files[root / ".qwen" / "skills" / skill_dir / "SKILL.md"] = qwen_skill(fields, body, mcp_names)

    for path in sorted(p for p in (root / "skills").rglob("*") if p.is_file() and p.name != "SKILL.md"):
        if any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts):
            continue
        # A skill refers to its support files by path from the skill directory, so the relative
        # path has to survive the copy, not just the file name.
        skill_dir = path.relative_to(root / "skills").parts[0]
        relative = path.relative_to(root / "skills" / skill_dir)
        for target in TARGETS:
            # A supporting file is copied as it is, including its line endings: the generator
            # does not reshape it, so decoding with universal newlines would turn a
            # Windows-authored script into a file that differs from the one it was copied from.
            files[root / target / "skills" / skill_dir / relative] = read_preserving_newlines(path)

    settings = json.dumps(
        build_hook_settings(root / "hooks" / "hooks.json"), indent=2, ensure_ascii=False
    ) + "\n"
    files[root / ".qwen" / "settings.json"] = settings
    return files


def is_generator_owned(path: pathlib.Path, root: pathlib.Path = ROOT) -> bool:
    """Whether a file inside a generated tree is the generator's own output.

    A generated tree is committed, so it can also hold files the generator did not make: a
    user's `settings.json`, a notes file, a skill directory the canonical source does not have.
    Cleaning stale output must not delete those, so only the shapes the generator itself writes
    are eligible.
    """
    parts = path.relative_to(root).parts
    if len(parts) < 2 or parts[0] not in TARGETS:
        return False
    rest = parts[1:]
    if rest[0] in ("agents", "commands"):
        return path.suffix == ".md"
    if rest[0] == "skills":
        # A generated skill directory mirrors a canonical skill, so everything under it is the
        # generator's copy. A directory the canonical source does not have is not something the
        # generator would ever write, so a file inside it is left alone.
        if len(rest) < 2 or not (root / "skills" / rest[1]).is_dir():
            return False
        # Qwen Code refuses a skill name its pattern cannot match, so the generator never writes
        # that skill into `.qwen/`, and a file it did not write there is not stale output.
        return parts[0] == ".opencode" or QWEN_SKILL_NAME.match(rest[1])
    return path == root / ".qwen" / "settings.json"


def create_only(path: pathlib.Path, root: pathlib.Path = ROOT) -> bool:
    """Qwen Code reads `settings.json` as user configuration, so an existing one is never rewritten."""
    return path == root / ".qwen" / "settings.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate .opencode/ and .qwen/ plugin trees")
    parser.add_argument("--check", action="store_true", help="fail if the generated trees are out of date")
    parser.add_argument("--root", default=str(ROOT), help="repository root whose canonical source to build from")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    generated = build(root)
    stale = [
        path
        for target in TARGETS
        for path in (root / target).rglob("*")
        if path.is_file() and path not in generated and is_generator_owned(path, root)
    ]
    kept = [
        path
        for target in TARGETS
        for path in (root / target).rglob("*")
        if path.is_file() and not is_generator_owned(path, root)
    ]

    drifted = []
    for path, content in sorted(generated.items()):
        expected = content.encode("utf-8")
        if not path.exists():
            drifted.append(path.relative_to(root))
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(expected)
            continue
        if create_only(path, root) and not args.check:
            # An existing settings.json belongs to whoever wrote it, so writing never overwrites it.
            # `--check` is a verification mode and the committed settings.json is the hooks source
            # rendered, so it is compared there; otherwise a change to hooks/hooks.json is invisible.
            continue
        # Compare bytes: a CRLF file is not the same file as the LF content the generator writes,
        # so --check reports it instead of treating the decoded text as equal.
        if path.read_bytes() != expected:
            drifted.append(path.relative_to(root))
            if not args.check:
                path.write_bytes(expected)

    if stale and not args.check:
        for path in stale:
            path.unlink()

    if args.check:
        problems = drifted + [p.relative_to(root) for p in stale]
        if problems:
            print(
                f"{len(problems)} generated files do not match the canonical source; "
                "run scripts/build_plugin_targets.py"
            )
            for path in sorted(problems):
                print(f"  {path}")
            if pathlib.Path(".qwen/settings.json") in problems:
                # Explaining this one is what makes the check actionable: settings.json is compared
                # because the committed copy is the hooks source rendered, but writing never
                # overwrites an existing file, so regenerating alone cannot fix it.
                print(
                    "  .qwen/settings.json is user configuration: put the hooks in hooks/hooks.json, "
                    "or remove the file so the generator can write it."
                )
            return 1
        print(f"{len(generated)} generated files are up to date")
        if kept:
            print(f"{len(kept)} files in the generated trees were not written by the generator and are left alone")
        return 0

    print(f"{len(generated)} files written")
    if drifted:
        print(f"{len(drifted)} changed")
    if stale:
        print(f"{len(stale)} stale files removed")
    if kept:
        print(f"{len(kept)} files in the generated trees not written by the generator, left alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
