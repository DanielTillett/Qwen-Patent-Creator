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
import os
import subprocess
from pathlib import Path

CLIENTS = ("claude", "opencode", "qwen-code", "none")
SCOPES = ("user", "project")

# The name a client shows in its MCP list, and in OpenCode the prefix of every
# tool name (<server>_<tool>). Deliberately different from upstream's so a
# co-installed fork cannot overwrite the entry.
SERVER_NAME = "qwen-patent-creator"


def resolve_client(explicit: str = "auto") -> str:
    """The client to register with: an explicit name, else the first installed CLI.

    The order is Qwen, OpenCode, Claude Code: this fork is Qwen-first, so a machine
    with more than one client installed should get the Qwen entry when the user did
    not say which client they meant. Claude is still a client someone can name, so
    it stays last rather than being removed from the list.
    """
    name = (explicit or "auto").strip().lower()
    if name in CLIENTS:
        return name
    if name != "auto":
        raise ValueError(f"Unknown client: {name}. Use one of {', '.join(CLIENTS)}, or 'auto'.")
    for command, client in (("qwen", "qwen-code"), ("opencode", "opencode"), ("claude", "claude")):
        if _cli_available(command):
            return client
    return "none"


def _cli_available(command: str) -> bool:
    try:
        return subprocess.run([command, "--version"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _config_home() -> Path:
    """OpenCode reads its user configuration from the XDG location, so `XDG_CONFIG_HOME` wins."""
    value = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(value) if value else Path.home() / ".config"


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
        base = project_root if scope == "project" else _config_home() / "opencode"
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
    rather than silently treated as missing. A file that has content but is not an
    object is malformed in the same way, and so is one the tool cannot read: both
    raise rather than looking absent, because "no entry" and "broken file" are
    different things to act on.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc

    kinds = _scan(text)
    root = _root_object(text, kinds)
    if root is None:
        if _has_content(text, kinds):
            raise ValueError(f"{path} is not a JSON object")
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

    try:
        raw = path.read_bytes()
    except OSError as exc:
        # A file that cannot be read is reported, not raised: a traceback from a config file the
        # tool was asked to edit reads as a crash rather than as a refusal.
        return False, f"Could not read {path}: {exc}"
    # A byte-order mark is dropped when the file is rewritten. JSONC parsers do not accept one,
    # so a file that had it could not be read by the client anyway; refusing to edit it would
    # only leave the user with a file nothing can open.
    text = raw.decode("utf-8-sig")
    # A file written on Windows keeps its line endings: editing it should not restyle the
    # lines the user did not change.
    crlf = "\r\n" in text and text.count("\n") == text.count("\r\n")

    kinds = _scan(text)
    if not _has_content(text, kinds):
        # Nothing but comments and whitespace: there is no document to merge into, so what the
        # user wrote is kept and the entry is appended after it. A comment-only file is treated
        # as an empty document, because a comment is not content.
        document = {name: entry}
        for key in reversed(keys):
            document = {key: document}
        body = json.dumps(document, indent=2) + "\n"
        if crlf:
            body = body.replace("\n", "\r\n")
        try:
            path.write_text(text + body, encoding="utf-8", newline="")
        except OSError as exc:
            return False, f"Could not write {path}: {exc}"
        return True, f"Wrote {path} (it had no document yet)"

    container = _root_object(text, kinds)
    if container is None:
        return False, f"{path} is not a JSON object; not editing it"

    for position, key in enumerate(keys):
        found = _find_key(text, kinds, key, inside=container)
        if found is None:
            value = {name: entry}
            for outer in reversed(keys[position + 1:]):
                value = {outer: value}
            return _insert_member(text, path, kinds, container, key, value, crlf)
        span = _value_span(text, kinds, found[1])
        if span is None or text[span[0]] != "{":
            return False, f"{path} has a {key} section that is not an object; not editing it"
        container = span

    existing = _find_key(text, kinds, name, inside=container)
    if existing is None:
        return _insert_member(text, path, kinds, container, name, entry, crlf)

    value = _value_span(text, kinds, existing[1])
    if value is None:
        return False, f"{path} has a {name} entry whose value cannot be read; not editing it"
    fragment = text[value[0]:value[1]]
    if _entry_matches(fragment, entry):
        return True, f"{path} already registers {name}; left unchanged"

    remote = _remote_field(client, fragment)
    if remote:
        # Qwen Code connects to a remote entry by its URL and ignores the command, so writing a
        # command beside it would report success while the client kept using the URL.
        return False, (
            f"{path} registers {name} with {remote}, which this client reads in preference to a "
            f"command; nothing was written. Remove {remote} to register the local server."
        )

    # Same name, different entry: rewrite only that value, so the comments and
    # the other servers around it stay as the user wrote them. Fields this client
    # does not read as part of the server shape (env, headers, disabled) are kept;
    # only the command and args it uses are changed. The value starts on the same
    # line as its key, so the continuation lines are indented from there.
    merged = _merge_entry(fragment, entry, shape_keys(client))
    if any(kinds[index] == "comment" for index in range(value[0], value[1])):
        # The user wrote comments inside this entry. Re-rendering the whole value would drop
        # them, so substitute only the fields being updated and leave the comments where they
        # were written. A comment inside one of the values being replaced is still lost: that
        # value has to be written as JSON, and JSON has no place for a comment.
        block = _rewrite_object_values(text, kinds, value, {
            key: merged[key] for key in shape_keys(client) if key in merged
        })
    elif "\n" in text[container[0] + 1:container[1]]:
        key_indent = _base_indent(text, value[0])
        lines = json.dumps(merged, indent=2).splitlines()
        block = lines[0]
        for line in lines[1:]:
            level = len(line) - len(line.lstrip())
            block += "\n" + key_indent + (" " * level if level else "") + line.lstrip()
    else:
        block = json.dumps(merged)
    new_text = text[:value[0]] + block + text[value[1]:]
    return _write(path, text, new_text, f"Updated the existing {name} entry in {path}", crlf)


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


def _rewrite_object_values(text: str, kinds: list, span: tuple, updates: dict) -> str:
    """Replace the value of named keys inside one object without losing the comments in it.

    The object is copied member by member, so a comment the user wrote inside it stays where it
    was; only the value of a key being updated is rewritten. A key the object does not have yet
    is added before the closing brace, after any real content, because a comment cannot end a
    member and the separator has to go between members, not between a member and a comment.

    Only the object's own members are updated: a key with the same name inside a nested value
    is a different member and is left as the user wrote it. A key written twice at this level is
    rewritten at every occurrence, because a JSON parser reads one object as the last duplicate
    wins and updating only the first would leave the value the client actually uses unchanged.
    """
    start, end = span
    pieces = [text[start]]
    seen = set()
    indent = _base_indent(text, start)
    member_indent = _member_indent(text, start, end)
    # The pieces written up to the last real content at this level, so a new member can be
    # inserted where the separator belongs instead of after a trailing comment.
    content_pieces = list(pieces)
    depth = 1
    index = start + 1
    while index < end:
        if kinds[index] == "string":
            string = _string_span(text, index)
            if depth > 1:
                # Inside a nested value: copy it, including its escaped characters.
                pieces.append(text[index:string[1]])
                index = string[1]
                continue
            colon = _value_start(text, kinds, string[1])
            if text[colon:colon + 1] == ":":
                key = json.loads(text[index:string[1]])
                if key in updates:
                    seen.add(key)
                    pieces.append(text[index:colon + 1])
                    # The value begins after the whitespace between the colon and it, so the span
                    # has to start there; from the character right after the colon a string value
                    # looks like an empty scalar and gets written twice.
                    value_start = _value_start(text, kinds, colon + 1)
                    value = _value_span(text, kinds, value_start)
                    pieces.append(text[colon + 1:value_start] or " ")
                    pieces.append(json.dumps(updates[key]))
                    content_pieces = list(pieces)
                    index = value[1] if value else value_start
                    continue
            pieces.append(text[index:string[1]])
            if depth == 1:
                content_pieces = list(pieces)
            index = string[1]
            continue
        char = text[index]
        if depth == 1 and index == end - 1:
            # The object's own closing brace, before this character is read as a closer: the
            # depth has to still be the object's own level here, or the keys the object does not
            # have yet are never added and the entry keeps the shape the user wrote.
            missing = [key for key in updates if key not in seen]
            if missing:
                last, content_end = _last_content(text, kinds, start, end)
                # A member needs a separator only when it is not the first one, and a trailing
                # comma the user wrote already separates the new member from the previous one.
                separator = "" if last in ("", "{", ",") else ","
                # A comment may sit between the last member and the closing brace: the separator
                # goes after real content, the new members after the comment, which is copied once.
                run = text[content_end:index]
                multiline = "\n" in run
                comment = ""
                if any(kinds[i] == "comment" for i in range(content_end, index)):
                    # A one-line object keeps the spacing the user wrote around its comment; a
                    # multiline one ends at the comment, so the new members start on a new line.
                    comment = run if not multiline else run.rstrip()
                joiner = "\n" + member_indent if multiline else " "
                rendered = []
                for key in missing:
                    block = json.dumps(key) + ": " + json.dumps(updates[key], indent=2 if multiline else None)
                    rendered.append(joiner + joiner.join(block.splitlines()))
                pieces = list(content_pieces)
                pieces.append(separator)
                pieces.append(comment)
                # The separator only separates the first new member from the content before it: the
                # new members themselves need commas between them.
                pieces.append(",".join(rendered))
                if multiline:
                    pieces.append("\n" + indent)
            pieces.append(char)
            break
        if char in "{[" and kinds[index] is None:
            depth += 1
        elif char in "}]" and kinds[index] is None:
            depth -= 1
        pieces.append(char)
        if depth == 1 and kinds[index] is None and not char.isspace():
            content_pieces = list(pieces)
        index += 1
    return "".join(pieces)


def _insert_member(text: str, path: Path, kinds: list, container: tuple, key, value, crlf: bool = False):
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

    return _write(path, text, new_text, f"Updated {path}", crlf)


def _write(path: Path, original: str, new_text: str, detail: str, crlf: bool = False):
    """Write only when the result still parses as JSONC."""
    try:
        _parse(new_text)
    except ValueError as exc:
        return False, f"Merging into {path} would make it invalid JSON ({exc}); nothing was written"
    if crlf:
        # A CRLF file is read with `newline=""`, so its line endings are already in the string;
        # converting only the newlines this merge introduces keeps the file CRLF without turning
        # an existing `\r\n` into `\r\r\n`.
        new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
    if new_text == original:
        return True, f"{path} unchanged"
    try:
        # newline="" keeps the line endings in the string as they are, so a CRLF file stays CRLF.
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
        return True, detail
    except OSError as exc:
        return False, f"Could not write {path}: {exc}"


def _entry_matches(fragment: str, entry: dict) -> bool:
    try:
        return _parse(fragment) == entry
    except ValueError:
        return False


def _remote_field(client: str, fragment: str) -> str:
    """The field in an existing entry that makes this client read it as a remote server.

    A file-configured client chooses its transport from the URL when one is present and ignores the
    command, so writing a command beside a URL would report success while the client kept using the
    URL: the user has to remove the field first. Qwen Code reads `httpUrl` or `url`; Claude Code
    reads `url` and its `type` is not part of the shape this tool rewrites for it. OpenCode chooses
    by `type`, which registration rewrites, so no leftover field wins there.
    """
    fields = {"qwen-code": ("httpUrl", "url"), "claude": ("url",)}.get(client, ())
    try:
        parsed = _parse(fragment)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    for field in fields:
        if field in parsed:
            return field
    return ""


def _next_significant(text: str, kinds: list, start: int) -> str:
    """The next character that is actual JSON content, not whitespace or a comment."""
    for index in range(start + 1, len(text)):
        if kinds[index] == "comment" or text[index].isspace():
            continue
        return text[index]
    return ""


def _has_content(text: str, kinds: list) -> bool:
    """Whether the document has anything a JSON parser would read.

    Comments and whitespace carry no value, so a file holding only them is an empty
    document rather than a malformed one: the entry can be appended without deciding
    what shape the file should have had.
    """
    return any(kinds[i] == "string" or (kinds[i] is None and not text[i].isspace())
               for i in range(len(text)))


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
                    # The escaped character is part of the string, whatever it is: an escaped
                    # quote does not end the string, so the comment or brace after it is read
                    # as it was written instead of being swallowed into the string.
                    index += 1
                    if index < len(text):
                        kinds[index] = "string"
                        index += 1
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
        # Only a comma the parser would read as a separator is a trailing one. A comma inside a
        # string is part of the value, so removing it would change what the client reads.
        is_comment = kinds[index] == "comment"
        is_trailing_comma = (
            kinds[index] is None and char == "," and _next_significant(text, kinds, index) in ("}", "]")
        )
        if is_comment or is_trailing_comma:
            cleaned.append(" ")
        else:
            cleaned.append(char)
    return json.loads("".join(cleaned))


def _find_key(text: str, kinds: list, key: str, inside: tuple = None):
    """Position of a key in an object: (key start, value start).

    When a key appears more than once at this level, the last occurrence is the one a JSON
    parser keeps, so that is the one returned: editing an earlier duplicate would leave the
    value the client reads unchanged.
    """
    span = inside or _root_object(text, kinds)
    if span is None:
        return None
    start, end = span
    depth = 1
    found = None
    index = start + 1
    while index < end:
        if kinds[index] == "comment" or text[index].isspace():
            index += 1
            continue
        if kinds[index] == "string":
            string = _string_span(text, index)
            if depth == 1 and _decoded_key(text, index, string[1]) == key:
                after = _value_start(text, kinds, string[1])
                if after < end and text[after] == ":":
                    found = (index, _value_start(text, kinds, after + 1))
                # A string that happens to equal `key` is a value, not a key:
                # keep walking instead of reporting the object as having no key.
            index = string[1]
            continue
        if text[index] in "{[":
            depth += 1
        elif text[index] in "}]":
            depth -= 1
        index += 1
    return found


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


def _decoded_key(text: str, start: int, end: int) -> str:
    """The key a JSON parser reads from a quoted string, escapes included.

    Comparing the raw spelling would miss `qw\\u0065n-patent-creator`, which names the same key as
    the plain form, and the tool would add a second entry while the client keeps reading the first.
    """
    try:
        return json.loads(text[start:end])
    except ValueError:
        return ""


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
    not after a comment that trails it. A string is content: the member that ends the
    object can be a string value, and the comma belongs after its closing quote.
    """
    for index in range(end - 2, start, -1):
        if kinds[index] == "comment" or text[index].isspace():
            continue
        return text[index], index + 1
    return "", start + 1
