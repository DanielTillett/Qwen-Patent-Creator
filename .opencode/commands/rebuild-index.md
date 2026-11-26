---
description: Rebuild the legal-reference index from downloaded sources
---

# Rebuild the Index

Check source and index locations under Storage:

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" health
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" health
```

Use those locations, including any `PATENT_DATA_DIR` override. PDFs are not normally in the plugin folder. Setup downloads missing sources and rebuilds the index:

macOS/Linux:
```bash
"$HOME/qwen-patent-creator-venv/bin/qwen-patent-creator" setup --rebuild
```

Windows PowerShell:
```powershell
& "$HOME\qwen-patent-creator-venv\Scripts\qwen-patent-creator.exe" setup --rebuild
```

Wait for the command to finish and inspect its exit status and errors, then run `health` again and try an MPEP search through your MCP client.

A rebuild reprocesses existing source files. It does not replace them with newer legal editions. For an edition update, obtain the intended official source, check its edition, replace the relevant file in the reported PDF directory, then rebuild. Keep a backup before replacing sources or rebuilding.

Use this after replacing source PDFs, changing the embedding model, or diagnosing index corruption. Build time depends on hardware and corpus size. See the **Index Manager** skill for memory settings and diagnostics.
