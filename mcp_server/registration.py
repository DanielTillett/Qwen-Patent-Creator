#!/usr/bin/env python3
"""Register the MCP server with the client that is actually being used (plan §3).

Upstream hardcodes Claude Code and ``--scope user``. The server itself is
client-agnostic, so registration is only a per-client config shape:

- Claude Code: the ``claude mcp add`` CLI, which stays in ``cli.py``.
- OpenCode V2: ``mcp.servers.<name>`` in ``opencode.json`` / ``opencode.jsonc``.
  V1 put server names directly under ``mcp``; that shape is read as a fallback.
- Qwen Code CLI: ``mcpServers.<name>`` in ``settings.json``.

Existing configuration is merged, never rewritten: comments, unknown keys and the
user's own formatting survive, so a JSONC file is still valid after the write.
"""

import json
import subprocess
from pathlib import Path

CLIENTS = ("claude", "opencode", "qwen-code", "none")
SCOPES = ("user", "project")

# The name a client shows in its MCP list, and in OpenCode the prefix of every
# tool name (<server>_<tool>). Deliberately different from upstream's so a
# co-installed fork cannot overwrite the entry.
SERVER_NAME = "qwen-patent-creator"


def resolve_client(explicit: str = "auto") -> str:
    """The client to register with: an explicit name, else the first installed CLI."""
    name = (explicit or "auto").strip().lower()
    if name in CLIENTS:
        return name
    if name != "auto":
        raise ValueError(f"Unknown client: {name}. Use one of {', '.join(CLIENTS)}, or 'auto'.")
    for command, client in (("claude", "claude"), ("opencode", "opencode"), ("qwen", "qwen-code")):
        if _cli_available(command):
            return client
    return "none"


def _cli_available(command: str) -> bool:
    try:
        return subprocess.run([command, "--version"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def config_path(client: str, scope: str, project_root: Path) -> Path:
    """Where this client's MCP configuration lives for this scope.

    An existing file wins over the default name: a project may use
    ``opencode.jsonc`` while the default would be ``opencode.json``.
    """
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope: {scope}. Use one of {', '.join(SCOPES)}.")
    if client == "claude":
        # `claude mcp add` owns this file, but verification reads it.
        base = project_root if scope == "project" else Path.home()
        names = [".claude.json" if scope == "user" else ".mcp.json"]
    elif client == "opencode":
        base = project_root if scope == "project" else Path.home() / ".config" / "opencode"
        names = ["opencode.jsonc", "opencode.json"]
    elif client == "qwen-code":
        base = project_root / ".qwen" if scope == "project" else Path.home() / ".qwen"
        names = ["settings.json"]
    else:
        raise ValueError(f"{client} has no client configuration file")

    for name in names:
        existing = base / name
        if existing.exists():
            return existing
    return base / names[0]


def server_entry(client: str, python_path: Path, server_script: Path) -> dict:
    """One server entry, in the shape this client expects."""
    if client == "opencode":
        # OpenCode takes the executable and its arguments as one array.
        return {"type": "local", "command": [str(python_path), str(server_script)]}
    if client in ("claude", "qwen-code"):
        return {"command": str(python_path), "args": [str(server_script)]}
    raise ValueError(f"{client} is not a file-configured client")


def command_and_args(entry: dict) -> tuple:
    """The interpreter and script an entry points at, in either shape."""
    command = entry.get("command", "")
    if isinstance(command, list):
        return (command[0] if command else ""), command[1:]
    return command, entry.get("args", [])


def section_key(client: str) -> tuple:
    """The nested keys a client expects servers under."""
    if client == "opencode":
        # V2 nests servers under `mcp.servers`; V1 put them directly under `mcp`.
        return ("mcp", "servers")
    return ("mcpServers",)


def read_entry(path: Path, client: str, name: str = SERVER_NAME):
    """The entry a client file registers for this server, or None if it is absent.

    Read-only: verification reports what the file says and never repairs it. A
    value that cannot be parsed raises, so a malformed file is shown to the user
    rather than silently treated as missing.
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    kinds = _scan(text)
    root = _root_object(text, kinds)
    if root is None:
        return None

    containers = [root]
    for key in section_key(client):
        found = _find_key(text, kinds, key, inside=containers[-1])
        if found is None:
            break
        span = _value_span(text, kinds, found[1])
        if span is None or text[span[0]] != "{":
            break
        containers.append(span)

    # An older OpenCode file may still hold the entry directly under `mcp`, so
    # the name is looked for at every level the walk passed through.
    for container in reversed(containers):
        found = _find_key(text, kinds, name, inside=container)
        if found is None:
            continue
        span = _value_span(text, kinds, found[1])
        if span is None:
            return None
        return _parse(text[span[0]:span[1]])
    return None


def register(client: str, scope: str, python_path: Path, server_script: Path,
            project_root: Path, name: str = SERVER_NAME):
    """Merge one server entry into the client's config. Returns (ok, detail)."""
    if client == "none":
        return True, "Registration skipped (--client none): nothing was written."

    path = config_path(client, scope, project_root)
    entry = server_entry(client, python_path, server_script)
    keys = list(section_key(client))

    if not path.exists():
        document = {name: entry}
        for key in reversed(keys):
            document = {key: document}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            return False, f"Could not write {path}: {exc}"
        return True, f"Created {path} with the server registered"

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        # An empty or whitespace-only file is an empty document, not a parse error.
        document = {name: entry}
        for key in reversed(keys):
            document = {key: document}
        try:
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            return False, f"Could not write {path}: {exc}"
        return True, f"Wrote {path} (it was empty)"

    kinds = _scan(text)
    container = _root_object(text, kinds)
    if container is None:
        return False, f"{path} is not a JSON object; not editing it"

    for position, key in enumerate(keys):
        found = _find_key(text, kinds, key, inside=container)
        if found is None:
            value = {name: entry}
            for outer in reversed(keys[position + 1:]):
                value = {outer: value}
            return _insert_member(text, path, kinds, container, key, value)
        span = _value_span(text, kinds, found[1])
        if span is None or text[span[0]] != "{":
            return False, f"{path} has a {key} section that is not an object; not editing it"
        container = span

    existing = _find_key(text, kinds, name, inside=container)
    if existing is None:
        return _insert_member(text, path, kinds, container, name, entry)

    value = _value_span(text, kinds, existing[1])
    if value is None:
        return False, f"{path} has a {name} entry whose value cannot be read; not editing it"
    fragment = text[value[0]:value[1]]
    if _entry_matches(fragment, entry):
        return True, f"{path} already registers {name}; left unchanged"

    # Same name, different entry: rewrite only that value, so the comments and
    # the other servers around it stay as the user wrote them. Fields this client
    # does not read as part of the server shape (env, headers, disabled) are kept;
    # only the command and args it uses are changed. The value starts on the same
    # line as its key, so the continuation lines are indented from there.
    merged = _merge_entry(fragment, entry, shape_keys(client))
    if "\n" in text[container[0] + 1:container[1]]:
        key_indent = _base_indent(text, value[0])
        lines = json.dumps(merged, indent=2).splitlines()
        block = lines[0]
        for line in lines[1:]:
            level = len(line) - len(line.lstrip())
            block += "\n" + key_indent + (" " * level if level else "") + line.lstrip()
    else:
        block = json.dumps(merged)
    new_text = text[:value[0]] + block + text[value[1]:]
    return _write(path, text, new_text, f"Updated the existing {name} entry in {path}")


def shape_keys(client: str) -> tuple:
    """The fields that carry the server shape for this client."""
    if client == "opencode":
        return ("type", "command")
    return ("command", "args")


def _merge_entry(fragment: str, entry: dict, keys: tuple) -> dict:
    """The new shape, keeping the user's other fields and their order."""
    try:
        parsed = _parse(fragment)
    except ValueError:
        return dict(entry)
    if not isinstance(parsed, dict):
        return dict(entry)

    merged = {}
    for key, value in parsed.items():
        merged[key] = entry[key] if key in keys else value
    for key, value in entry.items():
        merged.setdefault(key, value)
    return merged


def _insert_member(text: str, path: Path, kinds: list, container: tuple, key, value):
    """Add one member inside an existing object; the rest of the file is untouched."""
    start, end = container
    close = end - 1
    body = text[start + 1:close]
    last_content, content_end = _last_content(text, kinds, start, end)
    separator = "" if not last_content or last_content == "," else ","

    # A comment may sit between the last member and the closing brace. The comma
    # belongs after the last JSON content, not after the comment, so it goes at
    # `content_end`; the new member goes after the comment, before `}`.
    run = text[content_end:close]

    if body and "\n" not in body:
        member = json.dumps(key) + ": " + json.dumps(value)
        new_text = text[:content_end] + separator + run + " " + member + text[close:]
    else:
        # The whitespace between the last member and `}` is the indentation the
        # closing brace sits on: keep any comment there, then put the new member on
        # its own line at the indentation the object's members already use.
        indent = _member_indent(text, start, end)
        block = json.dumps(key) + ": " + json.dumps(value, indent=2)
        member = "\n".join(indent + line for line in block.splitlines())
        new_text = (
            text[:content_end] + separator + run.rstrip() + "\n" + member
            + "\n" + _base_indent(text, start) + text[close:]
        )

    return _write(path, text, new_text, f"Updated {path}")


def _write(path: Path, original: str, new_text: str, detail: str):
    """Write only when the result still parses as JSONC."""
    try:
        _parse(new_text)
    except ValueError as exc:
        return False, f"Merging into {path} would make it invalid JSON ({exc}); nothing was written"
    if new_text == original:
        return True, f"{path} unchanged"
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write {path}: {exc}"
    return True, detail


def _entry_matches(fragment: str, entry: dict) -> bool:
    try:
        return _parse(fragment) == entry
    except ValueError:
        return False


def _next_significant(text: str, kinds: list, start: int) -> str:
    """The next character that is actual JSON content, not whitespace or a comment."""
    for index in range(start + 1, len(text)):
        if kinds[index] == "comment" or text[index].isspace():
            continue
        return text[index]
    return ""


def _scan(text: str) -> list:
    """Mark each character as part of a string, a comment, or neither."""
    kinds = [None] * len(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            kinds[index] = "string"
            index += 1
            while index < len(text):
                kinds[index] = "string"
                if text[index] == "\\":
                    index += 1
                    if index < len(text):
                        kinds[index] = "string"
                    continue
                if text[index] == '"':
                    break
                index += 1
            index += 1
            continue
        if char == "/" and text[index + 1:index + 2] == "/":
            while index < len(text) and text[index] != "\n":
                kinds[index] = "comment"
                index += 1
            continue
        if char == "/" and text[index + 1:index + 2] == "*":
            # A block comment ends at `*/`, not at the first `/` inside it, so a
            # path or URL in the comment does not end it early. Both characters of
            # the closing `*/` are marked, or the `/` is left as JSON content.
            while index < len(text):
                kinds[index] = "comment"
                if text[index] == "*" and text[index + 1:index + 2] == "/":
                    kinds[index + 1] = "comment"
                    index += 2
                    break
                index += 1
            continue
        index += 1
    return kinds


def _parse(text: str):
    """Read JSON as JSONC does: comments and trailing commas are allowed."""
    kinds = _scan(text)
    cleaned = []
    for index, char in enumerate(text):
        if kinds[index] == "comment" or (char == "," and _next_significant(text, kinds, index) in ("}", "]")):
            cleaned.append(" ")
        else:
            cleaned.append(char)
    return json.loads("".join(cleaned))


def _find_key(text: str, kinds: list, key: str, inside: tuple = None):
    """Position of a key in an object: (key start, value start)."""
    span = inside or _root_object(text, kinds)
    if span is None:
        return None
    start, end = span
    depth = 1
    index = start + 1
    while index < end:
        if kinds[index] == "comment" or text[index].isspace():
            index += 1
            continue
        if kinds[index] == "string":
            string = _string_span(text, index)
            if depth == 1 and text[index + 1:string[1] - 1] == key:
                after = _value_start(text, kinds, string[1])
                if after < end and text[after] == ":":
                    return (index, _value_start(text, kinds, after + 1))
                # A string that happens to equal `key` is a value, not a key:
                # keep walking instead of reporting the object as having no key.
            index = string[1]
            continue
        if text[index] in "{[":
            depth += 1
        elif text[index] in "}]":
            depth -= 1
        index += 1
    return None


def _value_start(text: str, kinds: list, position: int) -> int:
    while position < len(text) and (kinds[position] == "comment" or text[position].isspace()):
        position += 1
    return position


def _value_span(text: str, kinds: list, start: int):
    """Span of a value beginning at `start`: (start, end)."""
    if start is None or start >= len(text):
        return None
    if kinds[start] == "string":
        return _string_span(text, start)
    char = text[start]
    if char not in "{[":
        end = start
        while end < len(text) and kinds[end] is None and text[end] not in ",}]\n":
            end += 1
        return (start, end)
    closer = "}" if char == "{" else "]"
    depth = 0
    index = start
    while index < len(text):
        if kinds[index] is None:
            if text[index] == char:
                depth += 1
            elif text[index] == closer:
                depth -= 1
                if depth == 0:
                    return (start, index + 1)
        index += 1
    return None


def _string_span(text: str, start: int):
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return (start, index + 1)
        index += 1
    return (start, len(text))


def _root_object(text: str, kinds: list):
    """Span of the document's object, or None when the document is not one.

    The first character that is neither whitespace nor a comment has to be `{`: an
    array root, or an object that only appears inside another value, is not a client
    configuration file.
    """
    start = _value_start(text, kinds, 0)
    if start >= len(text) or text[start] != "{":
        return None
    return _value_span(text, kinds, start)


def _member_indent(text: str, start: int, end: int) -> str:
    """Indentation for a new member of the object at `start`: match what is already there."""
    body = text[start + 1:end]
    for line in body.split("\n")[1:]:
        if line.strip():
            return line[:len(line) - len(line.lstrip())]
    return _base_indent(text, start) + "  "


def _base_indent(text: str, start: int) -> str:
    """Indentation of the line the object at `start` begins on."""
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    return prefix[:len(prefix) - len(prefix.lstrip())]


def _last_content(text: str, kinds: list, start: int, end: int) -> tuple:
    """Last JSON content character before the closing brace: (character, position after it).

    Comments and whitespace are skipped: a comment cannot end a member, so the comma
    that separates a new member from the previous one has to go after real content,
    not after a comment that trails it.
    """
    for index in range(end - 2, start, -1):
        if kinds[index] is None and not text[index].isspace():
            return text[index], index + 1
    return "", start + 1
