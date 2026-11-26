"""Per-client MCP registration and verification (plan §3).

Upstream assumes Claude Code and `~/.claude.json`. The server itself is
client-agnostic, so the only difference is the shape each client reads. What
matters is that a user's existing file survives: comments, their own
indentation, trailing commas, and the other servers in the same section.
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server import cli
from mcp_server import registration as reg

REPO_ROOT = Path(cli.__file__).resolve().parent.parent
# Verification compares against the interpreter and script this process would
# register, so the tests use the same values.
PYTHON = Path(sys.executable)
SERVER = REPO_ROOT / "mcp_server" / "server.py"
NAME = reg.SERVER_NAME


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory, so user-scope paths stay inside tmp_path.

    `XDG_CONFIG_HOME` is honoured for OpenCode, so it has to be redirected with it: with the
    developer's own value inherited, a user-scope test would write the real config file.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def written(client, scope, base, text, newline=None):
    """Write a client config file where that client would look for it."""
    path = reg.config_path(client, scope, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `newline=""` writes the string as it is, so a document written with CRLF is a CRLF file;
    # the default would translate every `\n` to the platform separator.
    path.write_text(text, encoding="utf-8", newline=newline)
    return path


def verify_args(client, scope, project=None):
    return SimpleNamespace(
        client=client,
        scope=scope,
        project_dir=str(project) if project else None,
    )


class TestClientResolution:
    def test_explicit_names_are_accepted(self):
        for name in reg.CLIENTS:
            assert reg.resolve_client(name) == name

    def test_unknown_client_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown client"):
            reg.resolve_client("cursor")

    def test_auto_picks_the_first_installed_cli(self, monkeypatch):
        monkeypatch.setattr(cli.registration, "_cli_available", lambda command: command == "opencode")
        assert cli.registration.resolve_client("auto") == "opencode"

    def test_auto_without_any_cli_is_none(self, monkeypatch):
        monkeypatch.setattr(cli.registration, "_cli_available", lambda command: False)
        assert cli.registration.resolve_client("auto") == "none"

    def test_none_has_no_config_file(self, home):
        with pytest.raises(ValueError, match="no client configuration file"):
            reg.config_path("none", "user", home)


class TestConfigPaths:
    def test_claude_files_by_scope(self, home):
        assert reg.config_path("claude", "user", home) == home / ".claude.json"
        assert reg.config_path("claude", "project", home) == home / ".mcp.json"

    def test_an_existing_file_beats_the_default_name(self, home, monkeypatch):
        # The default is `opencode.jsonc`, but a project or user that already has
        # `opencode.json` keeps it.
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        base = home / ".config" / "opencode"
        existing = base / "opencode.json"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("{}", encoding="utf-8")
        assert reg.config_path("opencode", "user", home) == existing

    def test_opencode_uses_xdg_config_home(self, home, monkeypatch):
        # OpenCode reads its user configuration from the XDG location, so a user who sets
        # XDG_CONFIG_HOME gets that location instead of `~/.config`, and a registration round
        # trip has to find the file it just wrote there.
        xdg = home / "xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        assert reg.config_path("opencode", "user", home) == xdg / "opencode" / "opencode.jsonc"
        # The other clients keep their own home-based files: XDG is OpenCode's convention.
        assert reg.config_path("qwen-code", "user", home) == home / ".qwen" / "settings.json"
        assert reg.config_path("claude", "user", home) == home / ".claude.json"

        path = written("opencode", "user", home, '{\n  "mcp": {\n    "servers": {}\n  }\n}\n')
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        assert reg.read_entry(path, "opencode") == {"type": "local", "command": [str(PYTHON), str(SERVER)]}

    def test_a_blank_xdg_config_home_falls_back_to_the_home_directory(self, home, monkeypatch):
        # An empty or whitespace-only XDG_CONFIG_HOME is not a location, so `~/.config` is used.
        monkeypatch.setenv("XDG_CONFIG_HOME", "   ")
        assert reg.config_path("opencode", "user", home) == home / ".config" / "opencode" / "opencode.jsonc"

    def test_qwen_code_reads_settings_json(self, home):
        assert reg.config_path("qwen-code", "user", home) == home / ".qwen" / "settings.json"

    def test_unknown_scope_is_rejected(self, home):
        with pytest.raises(ValueError, match="Unknown scope"):
            reg.config_path("opencode", "workspace", home)


class TestEntryShapes:
    def test_opencode_takes_command_as_an_array(self):
        assert reg.server_entry("opencode", PYTHON, SERVER) == {
            "type": "local",
            "command": [str(PYTHON), str(SERVER)],
        }

    def test_the_other_file_clients_use_command_and_args(self):
        for client in ("claude", "qwen-code"):
            assert reg.server_entry(client, PYTHON, SERVER) == {
                "command": str(PYTHON),
                "args": [str(SERVER)],
            }

    def test_none_is_not_a_file_configured_client(self):
        with pytest.raises(ValueError, match="not a file-configured client"):
            reg.server_entry("none", PYTHON, SERVER)

    def test_both_shapes_read_back_the_same_paths(self):
        array = reg.command_and_args(reg.server_entry("opencode", PYTHON, SERVER))
        string = reg.command_and_args(reg.server_entry("claude", PYTHON, SERVER))
        assert array == string == (str(PYTHON), [str(SERVER)])

    def test_opencode_nests_servers_under_mcp(self):
        assert reg.section_key("opencode") == ("mcp", "servers")
        assert reg.section_key("qwen-code") == ("mcpServers",)

    def test_a_windows_path_survives_the_json_write(self, home):
        # A Windows path is written as JSON string content, so its separators are
        # escaped in the file and read back as the same path.
        python = Path("C:\\Users\\dev\\venv\\Scripts\\python.exe")
        script = Path("C:\\Users\\dev\\Qwen-Patent-Creator\\mcp_server\\server.py")
        path = home / ".qwen" / "settings.json"
        ok, detail = reg.register("qwen-code", "user", python, script, home)
        assert ok, detail
        assert "\\\\" in path.read_text(encoding="utf-8")
        entry = reg.read_entry(path, "qwen-code")
        assert reg.command_and_args(entry) == (str(python), [str(script)])


class TestRoundTrips:
    def test_comments_survive_the_write(self, home):
        path = written(
            "opencode",
            "user",
            home,
            '{\n  // keep me\n  "mcp": {\n    "servers": {\n      "other": { "command": "x" },\n    },  // trailing comma\n  }\n}\n',
        )
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "// keep me" in text and "// trailing comma" in text
        assert reg.read_entry(path, "opencode") == {
            "type": "local",
            "command": [str(PYTHON), str(SERVER)],
        }

    def test_a_member_followed_by_a_comment_is_still_merged(self, home):
        # A comment cannot end a member, so the comma that separates the new member
        # from the previous one has to go after the JSON content, not after the comment.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "other": { "command": "x" } // note\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "// note" in text
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_block_comment_after_a_member_is_still_merged(self, home):
        # The closing `*/` is part of the comment: if the `/` is read as JSON content,
        # the comma lands in the wrong place and the file stops parsing.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "other": { "command": "x" } /* https://example.com */\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "/* https://example.com */" in text
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_document_that_starts_with_a_comment_is_read(self, home):
        path = written(
            "qwen-code", "project", home,
            "// top\n{\n  \"mcpServers\": {\n    \"" + NAME + "\": { \"command\": \"x\" }\n  }\n}\n",
        )
        assert reg.read_entry(path, "qwen-code") == {"command": "x"}
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        assert "// top" in path.read_text()

    def test_a_document_that_is_not_an_object_is_refused(self, home):
        # An array root, or an object only inside another value, is not a config file:
        # the first significant character has to be `{`.
        path = written("qwen-code", "project", home, "[{}]\n")
        before = path.read_text()
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert not ok and "not a JSON object" in detail, detail
        assert path.read_text() == before
        assert reg.read_entry(path, "qwen-code") is None

    def test_an_existing_entry_keeps_the_fields_this_client_does_not_use(self, home):
        # Merging replaces the shape this client reads, not the whole entry: the user's
        # own env and disabled flags survive.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": { "command": "old", "args": [], "env": { "X": "1" }, "disabled": true }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok and "Updated the existing" in detail, detail
        entry = reg.read_entry(path, "qwen-code")
        assert entry == {
            "command": str(PYTHON),
            "args": [str(SERVER)],
            "env": {"X": "1"},
            "disabled": True,
        }

    def test_a_url_in_a_string_is_not_read_as_a_comment(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "notes": "https://example.com/x",\n    }\n  }\n}\n',
        )
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        assert '"https://example.com/x"' in path.read_text()

    def test_an_empty_file_becomes_a_valid_document(self, home):
        path = written("opencode", "user", home, "{}")
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        assert reg.read_entry(path, "opencode") is not None

    def test_a_one_line_object_stays_one_line(self, home):
        path = written("opencode", "user", home, '{ "mcp": { "servers": { "other": { "command": "x" } } } }')
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        assert "\n" not in path.read_text()

    def test_the_indentation_the_user_used_is_kept(self, home):
        path = written(
            "opencode", "user", home,
            '{\n    "mcp": {\n        "servers": {\n            "other": { "command": "x" }\n        }\n    }\n}\n',
        )
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        line = [line for line in path.read_text().splitlines() if NAME in line][0]
        assert line.startswith("            "), line

    def test_an_existing_entry_is_rewritten_without_touching_its_neighbours(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "' + NAME + '": { "command": ["old"] },  // mine\n'
            '      "other": { "type": "local", "command": ["y"] },\n    }\n  }\n}\n',
        )
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok and "Updated the existing" in detail, detail
        text = path.read_text()
        assert '"old"' not in text
        assert '"other": { "type": "local", "command": ["y"] }' in text
        assert "// mine" in text
        assert reg.read_entry(path, "opencode") == {
            "type": "local",
            "command": [str(PYTHON), str(SERVER)],
        }

    def test_a_comment_inside_our_own_entry_is_kept_when_it_is_rewritten(self, home):
        # Re-rendering the whole value would drop the comments the user wrote inside the entry,
        # so only the fields being updated are substituted and the comments stay where they were.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "args": ["x"],\n'
            '      "env": { "A": "B" }  /* and this */\n'
            '    },\n    "other": { "command": "y" }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "// mine" in text and "/* and this */" in text
        assert '"old"' not in text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "args": [str(SERVER)],
            "env": {"A": "B"},
        }

    def test_running_it_twice_changes_nothing(self, home):
        path = written("opencode", "user", home, '{\n  "mcp": {\n    "servers": {}\n  }\n}\n')
        reg.register("opencode", "user", PYTHON, SERVER, home)
        first = path.read_text()
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok and "already registers" in detail, detail
        assert path.read_text() == first

    def test_a_section_that_is_not_an_object_is_refused(self, home):
        path = written("opencode", "user", home, '{\n  "mcp": {\n    "servers": []\n  }\n}\n')
        before = path.read_text()
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert not ok and "not an object" in detail, detail
        assert path.read_text() == before

    def test_a_file_that_cannot_be_parsed_is_never_written(self, home):
        path = written("opencode", "user", home, '{\n  "mcp": {\n    "servers": { "other": "abc\n  }\n}\n')
        before = path.read_text()
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert not ok, detail
        assert path.read_text() == before

    def test_an_unparseable_entry_is_replaced_and_the_file_stays_valid(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "' + NAME + '": { "command": bad },  // mine\n    }\n  }\n}\n',
        )
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "bad" not in text and "// mine" in text
        assert reg.read_entry(path, "opencode") == {
            "type": "local",
            "command": [str(PYTHON), str(SERVER)],
        }

    def test_a_broken_neighbour_stops_the_write(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "other": { "command": bad },\n      "' + NAME + '": { "type": "local", "command": ["old"] },\n    }\n  }\n}\n',
        )
        before = path.read_text()
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert not ok and "invalid JSON" in detail, detail
        assert path.read_text() == before

    def test_a_new_file_is_created_with_the_shape_opencode_reads(self, home):
        path = reg.config_path("opencode", "user", home)
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok and "Created" in detail, detail
        assert reg.read_entry(path, "opencode") == {
            "type": "local",
            "command": [str(PYTHON), str(SERVER)],
        }
        # A file written for OpenCode is not the shape Claude Code or Qwen Code reads.
        assert reg.read_entry(path, "claude") is None

    def test_an_escaped_quote_does_not_end_a_string(self, home):
        # An escaped quote is string content rather than the end of the string, so the comment
        # marker after it is still inside the value. Reading the quote as a closing one would
        # start a comment at the comma and the document would no longer parse.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "notes": "escaped ' + chr(92) + chr(34) + ' quote, /* not a comment */",\n'
            '    "' + NAME + '": { "command": "old" }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        assert '"escaped ' + chr(92) + chr(34) + ' quote, /* not a comment */"' in path.read_text(encoding="utf-8")
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_string_value_before_the_brace_is_content_the_separator_follows(self, home):
        # A comment does not end a member, but a string value does, so the comma that separates
        # the new member has to go after the string rather than before the closing brace.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "note": "ends here"\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert '"note": "ends here",' in text, text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "note": "ends here",
            "args": [str(SERVER)],
        }

    def test_a_comment_between_the_last_member_and_the_brace_is_copied_once(self, home):
        # The comment stays where the user wrote it and is not duplicated by the rewrite, while
        # the new members go after it.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "env": { "A": "B" }\n'
            '      /* trailing */\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert text.count("/* trailing */") == 1, text
        assert text.index("/* trailing */") < text.index('"args"'), text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "env": {"A": "B"},
            "args": [str(SERVER)],
        }

    def test_a_comment_inside_a_value_that_is_replaced_does_not_survive(self, home):
        """A comment written inside a member whose value is rewritten is gone, and that is the
        documented limit: only the comments outside the replaced value are kept.

        The value has to be re-rendered as JSON for the client to read the new command, so there
        is nowhere to put a comment inside it. A comment in a member this client does not rewrite
        is not affected and stays.
        """
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "args": [\n        "x" /* inside the replaced value */\n      ]\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "/* inside the replaced value */" not in text, text
        assert "// mine" in text, text
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_comment_in_a_member_that_is_not_rewritten_stays(self, home):
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "env": {\n        "A": "B" /* kept */\n      }\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert '"A": "B" /* kept */' in text, text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "env": {"A": "B"},
            "args": [str(SERVER)],
        }

    def test_a_trailing_comma_the_user_wrote_still_separates_the_new_member(self, home):
        # A trailing comma is already a separator, so adding another one before the new member
        # would leave `,,` and the file would stop parsing.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "env": { "A": "B" },\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert ",," not in text, text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "env": {"A": "B"},
            "args": [str(SERVER)],
        }

    def test_a_key_inside_a_nested_value_is_not_the_entrys_own_key(self, home):
        # Only the members at the entry's own level are updated: a nested object that happens to
        # have a key with the same name is the user's content and stays as written.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "old", // mine\n'
            '      "env": { "command": "nested, stays" }\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert '"command": "nested, stays"' in text, text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "env": {"command": "nested, stays"},
            "args": [str(SERVER)],
        }

    def test_every_occurrence_of_a_repeated_key_is_updated(self, home):
        # A JSON object that names the same key twice is unusual but parses, and the reader keeps
        # the last one; updating only the first would leave the value the client actually reads
        # pointing at the old command.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": {\n'
            '      "command": "first", // mine\n'
            '      "command": "second"\n'
            '    }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert '"first"' not in text and '"second"' not in text, text
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_file_written_with_crlf_stays_crlf(self, home):
        # Editing one value should not restyle the lines the user did not change, and the newlines
        # this merge adds have to be written as CRLF too rather than doubled.
        path = written(
            "qwen-code", "project", home,
            '{\r\n  "mcpServers": {\r\n    "' + NAME + '": {\r\n'
            '      "command": "old", // mine\r\n'
            '      "env": { "A": "B" }\r\n'
            '    }\r\n  }\r\n}\r\n',
            newline="",
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text(encoding="utf-8", newline="")
        assert text.count("\r\n") == text.count("\n"), text
        assert "\r\r\n" not in text, text
        assert reg.read_entry(path, "qwen-code") == {
            "command": str(PYTHON),
            "env": {"A": "B"},
            "args": [str(SERVER)],
        }

    def test_an_entry_that_is_only_a_comment_is_still_merged(self, home):
        # An entry with no members has nothing to rewrite, so the new members are inserted and the
        # comment the user wrote is kept where it was.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": { /* c */ }\n  }\n}\n',
        )
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        text = path.read_text()
        assert "/* c */" in text, text
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}


class TestARemoteEntryIsNotOverwritten:
    """A field the client reads in preference to a command has to be reported, not written over."""

    def test_a_qwen_entry_with_a_url_is_left_as_it_was(self, home):
        # Qwen Code connects to `httpUrl` and ignores `command`, so writing the command beside the
        # URL would report success while the client kept using the URL.
        path = written(
            "qwen-code", "project", home,
            '{\n  "mcpServers": {\n    "' + NAME + '": { "httpUrl": "https://example.com/mcp", "command": "old" }\n  }\n}\n',
        )
        before = path.read_text()
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert not ok and "httpUrl" in detail and "nothing was written" in detail, detail
        assert path.read_text() == before
        assert reg.read_entry(path, "qwen-code") == {
            "httpUrl": "https://example.com/mcp",
            "command": "old",
        }

    def test_the_same_entry_is_written_for_opencode_because_it_chooses_by_type(self, home):
        # OpenCode decides by `type`, which registration rewrites, so a leftover url field does not
        # win there and the entry can be updated.
        path = written(
            "opencode", "project", home,
            '{\n  "mcp": {\n    "servers": {\n      "' + NAME + '": { "type": "remote", "url": "https://example.com" }\n    }\n  }\n}\n',
        )
        ok, detail = reg.register("opencode", "project", PYTHON, SERVER, home)
        assert ok, detail
        assert reg.read_entry(path, "opencode") == {
            "type": "local",
            "url": "https://example.com",
            "command": [str(PYTHON), str(SERVER)],
        }


class TestReadEntry:
    def test_the_flat_mcp_shape_is_still_read(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "' + NAME + '": { "type": "local", "command": ["z"] }\n  }\n}\n',
        )
        assert reg.read_entry(path, "opencode") == {"type": "local", "command": ["z"]}

    def test_a_missing_file_reads_as_absent(self, home):
        assert reg.read_entry(home / "opencode.jsonc", "opencode") is None

    def test_a_malformed_value_raises_instead_of_looking_absent(self, home):
        path = written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "' + NAME + '": { "command": bad }\n    }\n  }\n}\n',
        )
        with pytest.raises(ValueError):
            reg.read_entry(path, "opencode")


class TestNoRegistrationAndExitCodes:
    def test_client_none_writes_nothing(self, home):
        ok, detail = reg.register("none", "user", PYTHON, SERVER, home)
        assert ok and "skipped" in detail, detail
        assert list(home.iterdir()) == []

    def test_configure_reports_the_skip_as_success(self, capsys):
        assert cli.configure_mcp_server("none", "user") is True
        assert "--client none" in capsys.readouterr().err

    def test_verify_config_exits_zero_for_no_client(self):
        # An observable acceptance criterion: a cold `setup --client none` and its
        # verification are a complete run, not a failure, so exit codes must say so.
        result = subprocess.run(
            [sys.executable, "-c", "from mcp_server import cli; raise SystemExit(cli.main())",
             "verify-config", "--client", "none"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "nothing to verify" in result.stderr.lower()

    def test_verify_config_accepts_the_file_it_wrote(self, home, monkeypatch):
        monkeypatch.setattr(cli, "_report_handshake", lambda *args, **kwargs: True)
        ok, detail = reg.register("opencode", "user", PYTHON, SERVER, home)
        assert ok, detail
        assert cli.verify_config_command(verify_args("opencode", "user")) == 0

    def test_verify_config_reports_a_file_it_cannot_parse(self, home, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_report_handshake", lambda *args, **kwargs: True)
        written(
            "opencode", "user", home,
            '{\n  "mcp": {\n    "servers": {\n      "' + NAME + '": { "command": bad }\n    }\n  }\n}\n',
        )
        assert cli.verify_config_command(verify_args("opencode", "user")) == 1
        assert "Invalid JSON" in capsys.readouterr().err

    def test_verify_config_reports_a_missing_entry_as_a_failure(self, home, capsys):
        written("opencode", "user", home, '{\n  "mcp": {\n    "servers": {}\n  }\n}\n')
        assert cli.verify_config_command(verify_args("opencode", "user")) == 1
        assert "No '" + NAME + "'" in capsys.readouterr().err

    def test_args_without_a_client_fall_back_to_auto(self, home, monkeypatch, capsys):
        # A bare `verify-config` with no client CLI installed must explain itself
        # instead of reading Claude's file.
        monkeypatch.setattr(cli.registration, "_cli_available", lambda command: False)
        assert cli.verify_config_command(SimpleNamespace()) == 0
        assert "Nothing to verify" in capsys.readouterr().err


class TestProjectScopeKeepsWhatIsAlreadyThere:
    """A project-scoped write adds one member; it does not replace the file.

    `.qwen/settings.json` is a single file that holds both the hooks the generated tree brings
    and the user's own settings, so registration has to insert its own `mcpServers` member and
    leave the rest of the document alone.
    """

    def test_an_existing_hooks_block_survives_the_write(self, home):
        settings = {
            "$schema": "https://qwen-code.github.io/qwen-code/settings.schema.json",
            "hooks": {
                "SessionStart": [
                    {"matcher": "Startup", "hooks": [{"type": "command", "command": "echo ready"}]}
                ]
            },
        }
        path = written("qwen-code", "project", home, json.dumps(settings, indent=2) + "\n")
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, home)
        assert ok, detail
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["hooks"] == settings["hooks"]
        assert document["$schema"] == settings["$schema"]
        assert reg.read_entry(path, "qwen-code") == {"command": str(PYTHON), "args": [str(SERVER)]}

    def test_a_project_write_lands_in_the_project_not_the_home_directory(self, home, tmp_path):
        project = tmp_path / "checkout"
        project.mkdir()
        path = reg.config_path("qwen-code", "project", project)
        assert path == project / ".qwen" / "settings.json"
        ok, detail = reg.register("qwen-code", "project", PYTHON, SERVER, project)
        assert ok, detail
        assert path.exists()
        assert not (home / ".qwen").exists()


class TestCommandLineChoices:
    """The flags the README documents have to be the flags the parser accepts."""

    def _help(self, monkeypatch, capsys, command: str) -> str:
        monkeypatch.setattr(sys, "argv", ["qwen-patent-creator", command, "--help"])
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 0
        return capsys.readouterr().out

    def test_setup_and_verify_advertise_the_same_clients_and_scopes(self, monkeypatch, capsys):
        for command in ("setup", "verify-config"):
            help_text = self._help(monkeypatch, capsys, command)
            for client in reg.CLIENTS + ("auto",):
                assert client in help_text, f"{command}: {client}"
            for scope in reg.SCOPES:
                assert scope in help_text, f"{command}: {scope}"

    def test_an_unregistered_client_is_refused_before_anything_is_written(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["qwen-patent-creator", "setup", "--client", "gemini"])
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_a_documented_choice_is_accepted_in_any_case(self, monkeypatch, capsys):
        # argparse lowercases before matching, so `--client NONE` is the documented no-op.
        monkeypatch.setattr(sys, "argv", ["qwen-patent-creator", "verify-config", "--client", "NONE"])
        assert cli.main() == 0
        assert "Nothing to verify" in capsys.readouterr().err
