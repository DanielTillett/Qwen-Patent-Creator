"""The setup subprocess that re-runs after PyTorch is reinstalled.

Setup re-execs itself in a fresh interpreter when it installs PyTorch, and the second pass
is the one that registers the server with a client. If the restart drops the client, the
scope or the project directory, the user asked for a project registration and got a user
one instead — so the restarted process has to see the same choices as the first one.
"""

from types import SimpleNamespace

import pytest

from mcp_server import cli


def capture_restart(monkeypatch, args):
    """Run setup with a reinstalling PyTorch step and return the re-exec argv and env."""
    monkeypatch.setattr(cli, "install_pytorch", lambda: True)
    seen = {}

    def fake_run(argv, env=None, **kwargs):
        seen["argv"] = list(argv)
        seen["env"] = dict(env or {})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.setup_command(args) == 0
    return seen


def test_restart_carries_the_client_scope_and_project(monkeypatch, tmp_path):
    args = SimpleNamespace(
        rebuild=True,
        no_hyde=True,
        non_interactive=True,
        client="opencode",
        scope="project",
        project_dir=str(tmp_path),
    )
    seen = capture_restart(monkeypatch, args)
    argv = seen["argv"]

    assert argv[:4] == [cli.sys.executable, "-m", "mcp_server.cli", "setup"]
    assert "--client" in argv and argv[argv.index("--client") + 1] == "opencode"
    assert "--scope" in argv and argv[argv.index("--scope") + 1] == "project"
    assert "--project-dir" in argv and argv[argv.index("--project-dir") + 1] == str(tmp_path)
    assert "--rebuild" in argv and "--no-hyde" in argv and "--non-interactive" in argv
    assert seen["env"].get("_PYTORCH_ALREADY_INSTALLED") == "1"


def test_the_restarted_process_sees_the_same_choices(monkeypatch, tmp_path):
    """The argv must be something the same parser accepts, and give back the same values."""
    cases = [
        SimpleNamespace(rebuild=True, no_hyde=True, non_interactive=True),
        SimpleNamespace(
            rebuild=False, no_hyde=False, non_interactive=True,
            client="qwen-code", scope="project", project_dir=str(tmp_path),
        ),
        SimpleNamespace(
            rebuild=False, no_hyde=False, non_interactive=True, client="none", scope="user"
        ),
    ]
    for args in cases:
        argv = capture_restart(monkeypatch, args)["argv"]
        parsed = cli.build_parser().parse_args(argv[3:])

        assert parsed.command == "setup"
        for field in ("rebuild", "no_hyde", "non_interactive", "client", "scope", "project_dir"):
            got = getattr(parsed, field, None)
            # A namespace built without the parser has no field at all; the parser's default
            # for it is what the first pass would have used, so those are equal too.
            expected = getattr(args, field, cli.build_parser().parse_args(["setup"]).__dict__[field])
            assert got == expected, field


def test_setup_accepts_the_clients_the_plugin_layer_targets(monkeypatch):
    parser = cli.build_parser()
    setup = parser._subparsers._group_actions[0].choices["setup"]
    client = [a for a in setup._actions if a.dest == "client"][0]
    assert set(client.choices) == {"claude", "opencode", "qwen-code", "auto", "none"}

    scope = [a for a in setup._actions if a.dest == "scope"][0]
    assert set(scope.choices) == {"user", "project"}

    # `none` is the no-registration path an acceptance run uses: it must exit 0, not error.
    parsed = parser.parse_args(["setup", "--client", "none", "--non-interactive"])
    assert parsed.client == "none"


@pytest.mark.parametrize("client", ["opencode", "qwen-code"])
def test_restart_quotes_a_client_name_the_parser_lowercases(monkeypatch, client):
    """`--client OpenCode` is stored lowercased, and the restart must pass the stored value."""
    parsed = cli.build_parser().parse_args(["setup", "--client", client.upper()])
    argv = capture_restart(monkeypatch, parsed)["argv"]
    assert argv[argv.index("--client") + 1] == client
