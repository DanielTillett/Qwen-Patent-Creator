#!/usr/bin/env python3
"""Rewrite the canonical plugin source to a client-neutral form.

The canonical ``agents/``, ``commands/`` and ``skills/`` trees are the single source of truth for
``scripts/build_plugin_targets.py``, so their frontmatter must not carry one client's vocabulary.
This script:

- consolidates ``allowed-tools`` into one neutral ``tools:`` field,
- drops ``model:`` (the values were Claude aliases such as ``sonnet``, which are not valid model ids
  for a Qwen client),
- maps built-in tool names to neutral tokens; MCP tool names (``check_package``, ``search_mpep``) are
  left as they are because the MCP server names them, not the client,
- maps backticked built-in tool names in prose to the same neutral tokens.

Run from the repository root.
"""

from __future__ import annotations

import pathlib
import re
import sys

NEUTRAL = {
    "Bash": "bash",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Grep": "grep",
    "Glob": "glob",
    "WebSearch": "websearch",
    "WebFetch": "webfetch",
    "Agent": "subagent",
    "Task": "subagent",
    "Skill": "skill",
    "TodoWrite": "todowrite",
}

DROPPED_FIELDS = {"model"}

# Fields are emitted in this order so generated output is byte-stable.
FIELD_ORDER = [
    "name",
    "description",
    "tools",
    "argument-hint",
    "priority",
    "paths",
    "user-invocable",
    "disable-model-invocation",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTMATTER = re.compile(r"\A---\n(?P<head>.*?)\n?---\n", re.DOTALL)


def parse_frontmatter(head: str) -> dict:
    fields: dict[str, object] = {}
    pending_list: str | None = None
    for line in head.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and pending_list is not None:
            fields[pending_list].append(line.strip()[2:].strip())
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in DROPPED_FIELDS:
            pending_list = None
            continue
        if key in ("tools", "allowed-tools"):
            key = "tools"
            value = value.strip()
            if value:
                fields[key] = [NEUTRAL.get(n.strip(), n.strip()) for n in value.split(",") if n.strip()]
            else:
                fields[key] = []
            pending_list = key
            continue
        if value.strip():
            fields[key] = value.strip()
            pending_list = None
        else:
            fields[key] = []
            pending_list = key
    return fields


def render(fields: dict) -> str:
    keys = [k for k in FIELD_ORDER if k in fields]
    keys += [k for k in fields if k not in FIELD_ORDER]
    lines = []
    for key in keys:
        value = fields[key]
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}: {', '.join(value)}")
        else:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def normalise(text: str) -> str:
    match = FRONTMATTER.match(text)
    if match:
        text = render(parse_frontmatter(match.group("head"))) + text[match.end():]

    def replace(m):
        token = m.group(1)
        return f"`{NEUTRAL.get(token, token)}`"

    return re.sub(r"`([A-Za-z_][A-Za-z_0-9]*)`", replace, text)


def main() -> int:
    changed = 0
    for path in sorted(ROOT.rglob("*.md")):
        parts = path.relative_to(ROOT).parts
        if not parts or parts[0] not in ("agents", "commands", "skills"):
            continue
        if parts[0] == "skills" and path.name != "SKILL.md":
            continue  # supporting files are copied verbatim
        original = path.read_text()
        updated = normalise(original)
        if updated != original:
            path.write_text(updated)
            changed += 1
            print(f"normalised {path.relative_to(ROOT)}")
    print(f"{changed} files changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
