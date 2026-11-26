# Contributing to Qwen Patent Creator

Thank you for your interest in contributing! This guide covers the essentials.

## Development Setup

```bash
# Clone and set up
git clone https://github.com/DanielTillett/Qwen-Patent-Creator.git
cd Qwen-Patent-Creator
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# Install with dev dependencies
pip install -e ".[dev]"

# Install PyTorch with GPU support (if applicable)
qwen-patent-creator setup
```

## Plugin Layer

Skills, agents and slash commands have one canonical source in `skills/`, `agents/` and `commands/`, written in
client-neutral frontmatter (a single `tools:` field, no `model:`). `scripts/build_plugin_targets.py` generates the
per-client trees the clients actually read — `.opencode/` for OpenCode and `.qwen/` for Qwen Code CLI — including the
per-client tool names, argument syntax and hooks.

Edit the canonical files, then run:

```bash
python scripts/build_plugin_targets.py        # regenerate
python scripts/build_plugin_targets.py --check  # CI does this; fails on drift
```

Commit the regenerated trees with the canonical change. Do not hand-edit `.opencode/` or `.qwen/`. The canonical
frontmatter rules are in `docs/QWEN_ADAPTATION_PLAN.md` §4.

## Branch Naming

| Prefix | Use For |
|--------|---------|
| `feature/` | New features |
| `bugfix/` | Bug fixes |
| `docs/` | Documentation changes |
| `perf/` | Performance improvements |
| `refactor/` | Code refactoring |

Example: `feature/add-cpc-classification-search`

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add CPC classification search to BigQuery tools
fix: prevent KeyError in severity ordering
docs: update GPU setup guide for CUDA 12.8
chore: add mypy configuration
perf: batch embedding generation for large indexes
refactor: extract shared validation logic
```

## Pull Request Process

1. Create a branch from `main`
2. Make your changes
3. Run linters: `ruff check .` and `black --check .`
4. Run tests: `pytest -m "not slow and not gpu and not api"`
5. Submit a PR using the [template](/.github/pull_request_template.md)
6. Address review feedback

## Code Style

Code style is enforced by tooling configured in `pyproject.toml`:

- **Formatting:** Black (line-length 100)
- **Linting:** Ruff
- **Import sorting:** isort (black profile)
- **Type checking:** mypy

Run all checks:
```bash
black .
ruff check . --fix
mypy mcp_server/
```

## Testing

```bash
# Run fast tests only
pytest -m "not slow and not gpu and not api"

# Run all tests
pytest

# Run with coverage
pytest --cov=mcp_server --cov-report=html
```

Test markers: `@pytest.mark.slow`, `@pytest.mark.gpu`, `@pytest.mark.integration`, `@pytest.mark.api`

## Adding MCP Tools

Follow the existing pattern in `mcp_server/tools/`:

```python
@mcp.tool()
@validate_input(YourInputModel)  # Pydantic v2 model
@track_performance
def your_tool(param: str) -> dict:
    """Tool description (the client sees this docstring)."""
    return {"result": "data"}
```

## Questions?

Open a [Question issue](https://github.com/RobThePCGuy/Claude-Patent-Creator/issues/new?template=question.md) or check the [README](README.md).
