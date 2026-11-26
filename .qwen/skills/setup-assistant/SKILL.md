---
name: setup-assistant
description: Guides through installation, configuration, and first-time setup of the Qwen Patent Creator system.
---

# Setup Assistant Skill

Expert system for installing, configuring, and setting up the Qwen Patent Creator MCP server.

## When to Use

Installing first time, setting up new environment, configuring authentication, troubleshooting installation, migrating to new machine, updating dependencies, verifying health.

## Quick Setup (about an hour, mostly waiting)

Follow the README's Quick Start; it is the tested path. In short:

```bash
# Mac/Linux (Windows: py -m venv qwen-patent-creator-venv, then .\qwen-patent-creator-venv\Scripts\...)
cd ~
python3 -m venv qwen-patent-creator-venv
qwen-patent-creator-venv/bin/pip install git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
qwen-patent-creator-venv/bin/qwen-patent-creator setup
```

Setup takes 25-40 minutes without an NVIDIA GPU and ends by testing the connection to your MCP client. Then restart your MCP client and ask: "Search MPEP for claim definiteness". From a plugin, `/setup-patent-system` does the same steps into the same folder.

## Complete Setup Lifecycle

### Phase 1: Pre-Installation Checks

**Requirements:**
- Python 3.10-3.13 (CI tests 3.11; 3.13 walked end to end)
- 8GB+ RAM (16GB recommended)
- 5GB free disk
- Optional: NVIDIA GPU with CUDA 12.x

**Verify:**
```bash
python3 --version  # Should show 3.10-3.13
nvidia-smi        # Optional: Check GPU
```

### Phase 2: Virtual Environment and Install

Use the README's folder, `~/qwen-patent-creator-venv`, so every README instruction works as written. Nothing needs activating; call the venv's commands by path.

```bash
# Mac/Linux
cd ~
python3 -m venv qwen-patent-creator-venv
qwen-patent-creator-venv/bin/pip install git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
# Windows (PowerShell, from cd ~)
py -m venv qwen-patent-creator-venv
.\qwen-patent-creator-venv\Scripts\pip install git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
```

From a source checkout instead: `pip install -e .` inside a venv made in the checkout.

### Phase 3: Setup

```bash
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup     # Windows: .\qwen-patent-creator-venv\Scripts\qwen-patent-creator setup
```

Handles GPU detection (installs the CUDA PyTorch build on NVIDIA and restarts itself), downloads, the index build (25-40 min without an NVIDIA GPU, with progress lines), Graphviz (asks first), BigQuery detection, and registration with a live connection test.

**GPU repairs:** let setup select the wheel for the detected GPU. Older NVIDIA cards need different wheels from recent cards. Follow `docs/GPU_SETUP.md` in the source checkout and test an actual CUDA operation before claiming GPU acceleration works.

### Phase 4: BigQuery Authentication (Optional)

BigQuery adds 100M+ patent search but is NOT required. MPEP search, claims review, EPO analysis, and all other features work without it.

The setup auto-detects existing gcloud credentials. If already authenticated, BigQuery works automatically.

**If the user wants to enable BigQuery later:**
```bash
# One command — opens browser for Google sign-in (free, no credit card)
! gcloud auth application-default login   # typed in the your MCP client prompt; in a plain terminal drop the "!"
```

**Do NOT present BigQuery as a failure or required step.** It is a bonus feature.

### Phase 5: MCP Server Registration

Setup registers the server and tests the connection itself. To recheck: `~/qwen-patent-creator-venv/bin/qwen-patent-creator verify-config`.

**Manual:**
```bash
# Get paths
~/qwen-patent-creator-venv/bin/qwen-patent-creator verify-config

# Register, letting setup write the entry for the client you use
qwen-patent-creator setup --client auto --scope user   # or claude / opencode / qwen-code

# Manual, for Claude Code only (use forward slashes!). Claude Code is still a client
# setup can register, but this fork ships no Claude plugin layer: only the MCP server.
claude mcp add --transport stdio qwen-patent-creator --scope user -- \
  "C:/path/to/venv/Scripts/python.exe" \
  "C:/path/to/mcp_server/server.py"

# OpenCode and Qwen Code read a JSON file instead: .opencode/opencode.jsonc and .qwen/settings.json,
# written by setup under --scope project, or in the user config with --scope user.

# Verify
opencode mcp list or qwen mcp list
```

**Critical:** Restart your MCP client after registration!

### Phase 6: Configuration Files

Nothing is required. Save optional keys with the config command (they go in `~/.config/qwen-patent-creator/config.json`, or `%APPDATA%\qwen-patent-creator\config.json` on Windows):

```bash
~/qwen-patent-creator-venv/bin/qwen-patent-creator config set SERPAPI_API_KEY "key"        # patent search, free 250/month (recommended)
~/qwen-patent-creator-venv/bin/qwen-patent-creator config set GOOGLE_CLOUD_PROJECT my-proj # optional BigQuery search
~/qwen-patent-creator-venv/bin/qwen-patent-creator config list                             # everything, with sources
```

ANTHROPIC_API_KEY / OPENAI_API_KEY are only for HyDE with HYDE_BACKEND=api, which is off by default. A `.env` in a source checkout also works.

### Phase 7: Health Check

```bash
~/qwen-patent-creator-venv/bin/qwen-patent-creator health      # Windows: .\qwen-patent-creator-venv\Scripts\qwen-patent-creator health
```

### Phase 8: Present Results

**Setup is complete when MPEP index is built and MCP server is registered.** BigQuery and EPO OPS are optional enhancements.

Present a clean summary table showing what's ready. Do NOT dump manual setup instructions for optional features. The system is fully functional for:
- MPEP/USC/CFR/EPC/PCT legal search
- Claims analysis (US + EPO)
- Specification analysis (US + EPO)
- Formalities checking (US + EPO + PCT)
- Patent diagram generation (once Graphviz is installed)
- Patent application drafting

Patent search needs SerpApi (recommended), EPO OPS, or BigQuery. Use the matching status tool before reporting readiness; EPO OPS is a real fallback when Google Patents is not ready. USPTO ODP only supplements this search. If none is ready, report the status errors and point to setup (README Quick Start, Step 3 for SerpApi).

**Test with:**
```
"Search MPEP for claim definiteness requirements"
```

**If this works -> Setup complete. Tell the user they're ready.**

## Common Setup Issues

| Issue | Solution |
|-------|----------|
| PyTorch CPU-only | Reinstall PyTorch FIRST |
| MCP not loading | Restart your MCP client, verify with `opencode mcp list or qwen mcp list` |
| Path errors | Use forward slashes (/) not backslashes (\\) |
| BigQuery fails | Re-auth: `gcloud auth application-default login` |
| Index not found | Build: `~/qwen-patent-creator-venv/bin/qwen-patent-creator setup --rebuild` |
| Import errors | Run the venv's own commands (`~/qwen-patent-creator-venv/bin/...`); nothing needs activating |

## Platform-Specific Notes

### Windows
- PowerShell: call `.\qwen-patent-creator-venv\Scripts\qwen-patent-creator` from `cd ~`; no activation needed
- Git Bash required for MCP commands
- Paths: Always forward slashes in MCP config
- CUDA: Install NVIDIA drivers + toolkit

### Linux
- venv: call `~/qwen-patent-creator-venv/bin/qwen-patent-creator`; no activation needed
- FAISS-GPU: Available on Linux only
- Permissions: May need sudo

### macOS
- Apple Silicon: builds the index on the CPU (faster than MPS for this job, 25-40 min)
- Intel: Use CPU or external GPU
- Homebrew: May need for dependencies

## Update & Maintenance

### Updating Dependencies
```bash
~/qwen-patent-creator-venv/bin/pip install --upgrade git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup     # re-checks, rebuilds only if needed
```

From a source checkout: `git pull`, then `venv/bin/pip install -e . --upgrade` and `venv/bin/python -m pytest -q`.

### Rebuilding Index
```bash
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup --rebuild
# About 5 minutes with an NVIDIA GPU, 25-40 minutes otherwise
```

### Re-registering MCP
```bash
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup   # re-registers and retests; downloads and index are kept
# Restart your MCP client
```

## Quick Reference

### Essential Commands
```bash
# Setup
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup
~/qwen-patent-creator-venv/bin/qwen-patent-creator health
opencode mcp list or qwen mcp list

# Maintenance
~/qwen-patent-creator-venv/bin/qwen-patent-creator setup --rebuild
~/qwen-patent-creator-venv/bin/qwen-patent-creator verify-config

# Source checkout only
venv/bin/python -m pytest -q
```

### Critical Files
- `~/.config/qwen-patent-creator/config.json` - saved settings and keys (`~/qwen-patent-creator-venv/bin/qwen-patent-creator config path`)
- `~/.local/share/qwen-patent-creator/data/` - downloaded sources and the search index (`PATENT_DATA_DIR` moves it)
- `.env` - environment variables, source checkout only
- `pdfs/` - MPEP PDF files
