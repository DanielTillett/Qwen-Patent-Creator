---
description: Install the package, download legal sources and register the MCP server
---

# Setup Patent Creator

Use Python 3.10-3.13, an internet connection and about 4 GB of free disk space. Use the home-folder venv from the README. If it already contains a working install, continue with setup.

## Install

macOS/Linux (use an installed supported Python, for example `python3.13`):
```bash
python3.13 -m venv "$HOME/qwen-patent-creator-venv"
"$HOME/qwen-patent-creator-venv/bin/python" -m pip install --upgrade pip
"$HOME/qwen-patent-creator-venv/bin/python" -m pip install git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
```

Windows PowerShell:
```powershell
py -3.13 -m venv "$HOME\qwen-patent-creator-venv"
& "$HOME\qwen-patent-creator-venv\Scripts\python.exe" -m pip install --upgrade pip
& "$HOME\qwen-patent-creator-venv\Scripts\python.exe" -m pip install git+https://github.com/DanielTillett/Qwen-Patent-Creator.git
```

Check each command's exit status before continuing. PowerShell uses `$LASTEXITCODE` for native programs. Do not announce installation success after a failure. No activation or change of working directory is needed.

## Setup

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" setup --non-interactive
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" setup --non-interactive
```

Setup checks GPU hardware, downloads missing legal sources, builds the index and registers the MCP server with a connection test. This can take tens of minutes. Monitor progress until it exits; read and resolve failures before claiming setup is complete.

Non-interactive setup does not install system Graphviz. If it is missing and diagrams are needed, explain the system package change and run the platform command printed by setup when authorized.

## Verify

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" health
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" health
```

Restart your MCP client, then ask it to search MPEP for claim definiteness. Confirm results contain source references.

## Enable patent search

SerpApi is the default. Obtain a key at https://serpapi.com/manage-api-key and save it without echoing it into chat:

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" config set SERPAPI_API_KEY "your-key"
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" config set SERPAPI_API_KEY "your-key"
```

Restart your MCP client and call `check_google_patents_status`, then try a patent search. The accepted config key is `SERPAPI_API_KEY`.

If Google Patents is not ready, EPO OPS is a patent-search fallback: configure `EPO_OPS_KEY` and `EPO_OPS_SECRET`, restart and call `check_epo_api_status`. USPTO ODP is a supplement only. BigQuery is optional; use `/configure-bigquery` only when wanted. Missing patent-search credentials do not prevent legal-reference searches or analyzer use; describe patent search as unconfigured.

## Recovery

- Failed download: rerun setup. Existing sources are kept; this does not update their editions.
- GPU trouble: follow `docs/GPU_SETUP.md` in the plugin root. Setup selects wheels for the detected GPU; do not install a generic CUDA wheel blindly.
- Out of memory: save a smaller batch and rerun setup:

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" config set PATENT_EMBED_BATCH_SIZE 16
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" config set PATENT_EMBED_BATCH_SIZE 16
```

If needed, save `PATENT_MPEP_DEVICE` as `cpu` using the same `config set` command. Remove either saved override with `config unset KEY` when it is no longer needed. Rebuild with `/rebuild-index`.
