"""The installed identity: package name, version and command name.

The fork's identity is what a user installs and runs, so the three places it
appears have to agree: `pyproject.toml`, `mcp_server/_version.py` (which reads
pyproject.toml rather than hardcoding a number) and `mcp_server/cli_command.py`
(which tells people which command to type). The CI version guard assumes the
version lives in pyproject.toml, so a disagreement here would make that guard
check a file that no longer decides anything.
"""

import re
import subprocess
from pathlib import Path

import tomllib

from mcp_server import _version, cli_command

REPO_ROOT = Path(__file__).resolve().parents[1]


def pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


class TestInstalledIdentity:
    def test_version_comes_from_pyproject(self):
        # _version.py must not carry its own number, or the CI guard would be
        # checking a file that no longer decides the version.
        source = (REPO_ROOT / "mcp_server" / "_version.py").read_text(encoding="utf-8")
        assert '__version__ = "' not in source
        assert _version.__version__ == pyproject()["project"]["version"]

    def test_package_name_matches(self):
        assert pyproject()["project"]["name"] == _version.PACKAGE_NAME

    def test_the_command_people_are_told_to_run_is_installed(self):
        scripts = pyproject()["project"]["scripts"]
        assert list(scripts) == [_version.PACKAGE_NAME]
        assert cli_command.installed_cli().endswith(_version.PACKAGE_NAME)
        assert _version.PACKAGE_NAME in cli_command._BARE.pattern

    def test_import_package_is_unchanged(self):
        # The import package stays `mcp_server`, so the fork and upstream cannot
        # share one environment; that is documented, and the packaging must match it.
        include = pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
        assert include == ["mcp_server*"]


class TestPackagedFiles:
    def test_every_non_python_file_in_the_package_is_packaged(self):
        """A data file inside mcp_server/ is invisible to an installed copy unless package-data lists it."""
        package_data = pyproject()["tool"]["setuptools"]["package-data"]["mcp_server"]
        patterns = {p for p in package_data if "*" in p}
        literals = {p for p in package_data if "*" not in p}
        for path in (REPO_ROOT / "mcp_server").rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            name = path.name
            if name.endswith(".py"):
                continue
            assert name in literals or any(name.endswith(p.replace("*", "")) for p in patterns), path

    def test_plugin_trees_are_not_installed(self):
        """The generated trees are read by the client from a checkout, not from site-packages.

        Documented in docs/QWEN_ADAPTATION_PLAN.md §5: `pip install` ships the MCP
        server, so a skill script must not depend on files that a wheel does not carry.
        """
        manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for group in ("skills", "agents", "commands", ".opencode", ".qwen"):
            assert group not in manifest, group
        assert not (REPO_ROOT / "mcp_server" / "skills").exists()


class TestTheVersionGuardChecksTheRightFile:
    def test_plugin_guard_reads_the_file_that_decides_the_version(self):
        """The guard cannot look for a bump in _version.py: that file has no number to bump.

        It reads [project].version, so a bump made only in pyproject.toml is the bump
        the guard has to see.
        """
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        guard = ci.split("jobs:")[1].split("  plugin-trees-current:")[0]
        # The version is read from the [project] section and compared as a value between the base
        # revision and HEAD, so a reformatted pyproject.toml or a single-quoted version is still seen.
        assert 'sed -n \'/^\\[project\\]/,/^\\[/p\' "$1"' in guard
        assert 'git show "$BASE:pyproject.toml"' in guard
        assert '[ "$OLD" != "$NEW" ]' in guard
        assert "_version.py" not in guard

    def test_the_guard_needs_an_actual_change_to_the_version_value(self, tmp_path):
        """A whitespace-only edit to the version line is not a bump, so the guard must still fail."""
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        helper = re.search(r"value\(\) \{[^}]+\}", ci).group(0)
        base = tmp_path / "base.toml"
        head = tmp_path / "head.toml"
        base.write_text('[project]\nversion = "0.4.2"\n[tool.ruff]\n', encoding="utf-8")
        head.write_text('[project]\nversion  =  "0.4.2"\n[tool.ruff]\n', encoding="utf-8")
        script = f'{helper}\nOLD=$(value "$1")\nNEW=$(value "$2")\n[ "$OLD" != "$NEW" ]\n'
        reformatted = subprocess.run(["bash", "-c", script, "bash", str(base), str(head)])
        assert reformatted.returncode != 0, "the same version written differently is not a bump"
        head.write_text("[project]\nversion = '0.4.3'\n[tool.ruff]\n", encoding="utf-8")
        bumped = subprocess.run(["bash", "-c", script, "bash", str(base), str(head)])
        assert bumped.returncode == 0, "a single-quoted version is read as the version"

    def test_generated_trees_are_checked_in_ci(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "build_plugin_targets.py --check" in ci
        # The canonical source and the generated trees are both watched, so a stale
        # committed tree cannot pass while the source is only checked by the test suite.
        guard = ci.split("jobs:")[1].split("  plugin-trees-current:")[0]
        for watched in ("skills/", "agents/", "commands/", "hooks/", ".opencode/", ".qwen/"):
            assert watched in guard.split("wc -l")[0], watched

