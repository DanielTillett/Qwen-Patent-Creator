#!/usr/bin/env python3
"""Point the instruction files at the fork's own app directory and console script.

The canonical plugin source, the docs and the README told readers to run the upstream
``patent-creator`` command out of ``~/patent-creator-venv``. The fork installs as
``qwen-patent-creator`` and stores its corpus and config under its own app-data folder,
so those lines have to name the fork.

Only the command and directory names change. The agent named ``patent-creator`` is a
prompt identity, not a command, so prose such as "the patent-creator subagent" is left
alone, as is CHANGELOG history. The fork's app-data folder, config file and MCP server
entry are also renamed, so the lowercase ``claude-patent-creator`` paths and the server
name follow the fork identity; the upstream repository URLs keep their capitalised name.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SUBCOMMANDS = (
    "config",
    "health",
    "setup",
    "status",
    "serve",
    "verify-config",
    "check-bigquery",
    "version",
    "--version",
)

SUBCOMMAND_TAIL = r"""['`]? (?:{})""".format("|".join(SUBCOMMANDS))

# In a raw string one backslash is already a literal backslash, so a character class that
# wants `/` and the Windows separator is written `[/\\]`: the class holds the `/` and one
# escaped backslash. Writing `[/\\\\]` reads as a doubled escape and suggests the pattern
# expects `\\` in the text, which documented Windows paths do not contain.
SEPARATOR = r"[/\\]"

PATTERNS = [
    (re.compile(r"(?<![\w-])patent-creator-venv"), "qwen-patent-creator-venv"),
    # Both separators: the Windows install writes `Scripts\qwen-patent-creator.exe`, and
    # a pattern that only knows `/` left 22 documented paths pointing at the upstream name.
    # The replacement is a function, not a string, because the matched prefix (`bin` or
    # `Scripts`) has to survive; a literal replacement string would have to rewrite it.
    (
        re.compile(r"(?:bin|Scripts)" + SEPARATOR + r"patent-creator(?:\.exe)?"),
        lambda m: m.group(0).replace("patent-creator", "qwen-patent-creator"),
    ),
    (re.compile(r"(?<![\w-])patent-creator(?=" + SUBCOMMAND_TAIL + r")"), "qwen-patent-creator"),
    (re.compile(r"(?<![\w-])patent-creator(?=" + SEPARATOR + r")"), "qwen-patent-creator"),
    (re.compile(r"(?<![\w-])claude-patent-creator(?=[.]" + SEPARATOR + r")"), "qwen-patent-creator"),
    (re.compile(r"(?<![\w-])claude-patent-creator(?= MCP server)"), "qwen-patent-creator"),
]

TARGETS = ["README.md", "CONTRIBUTING.md", "AGENTS.md", "docs"]
for group in ("agents", "commands", "skills"):
    TARGETS.append(group)

# The adaptation plan quotes the upstream identity as it was at the fork point, so it is
# a record rather than an instruction file and is not rewritten. Comparing the path in its
# `/` form (`as_posix`) rather than `str()` means a Windows checkout, where relative paths
# print with `\`, still recognises the exclusion.
EXCLUDED = {"docs/QWEN_ADAPTATION_PLAN.md"}


def is_excluded(path: pathlib.Path, root: pathlib.Path = ROOT) -> bool:
    """Whether a file is a record rather than an instruction, and so not rewritten."""
    return path.relative_to(root).as_posix() in EXCLUDED


def rewrite(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def main() -> int:
    # The targets are resolved from the repository root, so running from anywhere else
    # would write to paths outside the checkout.
    if pathlib.Path.cwd() != ROOT:
        print(f"Run from the checkout root ({ROOT}); the current directory is {pathlib.Path.cwd()}", file=sys.stderr)
        return 1

    changed = []
    for entry in TARGETS:
        path = ROOT / entry
        if path.is_dir():
            files = [
                p
                for p in sorted(path.rglob("*.md"))
                if "__pycache__" not in p.parts and is_excluded(p, ROOT) is False
            ]
        else:
            files = [path]
        for file in files:
            if not file.is_file():
                continue
            # Read without universal-newline translation: a CRLF document read with the
            # default newline handling looks like LF, and the rewrite would then quietly
            # flatten a Windows-authored file on the way back out.
            with file.open(encoding="utf-8", newline="") as handle:
                original = handle.read()
            updated = rewrite(original)
            if updated != original:
                # The rewrite only changes names, never line endings, so writing with no
                # translation reproduces the file's own style.
                file.write_text(updated, encoding="utf-8", newline="")
                changed.append(str(file.relative_to(ROOT)))
    print(f"{len(changed)} files changed")
    for name in changed:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
