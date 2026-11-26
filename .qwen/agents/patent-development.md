---
name: patent-development
description: Develop and extend the patent creator MCP server, analyzers and plugin workflows
tools:
  - read_file
  - write_file
  - edit
  - run_shell_command
  - grep_search
  - glob
  - skill
  - mcp__qwen-patent-creator__review_patent_claims
  - mcp__qwen-patent-creator__review_specification
  - mcp__qwen-patent-creator__check_formalities
  - mcp__qwen-patent-creator__check_bigquery_status
  - mcp__qwen-patent-creator__search_patents_bigquery
  - mcp__qwen-patent-creator__get_patent_bigquery
  - mcp__qwen-patent-creator__get_patents_bigquery
  - mcp__qwen-patent-creator__search_patents_by_cpc_bigquery
  - mcp__qwen-patent-creator__search_patents_by_ipc_bigquery
  - mcp__qwen-patent-creator__search_patent_family_bigquery
  - mcp__qwen-patent-creator__render_diagram
  - mcp__qwen-patent-creator__create_flowchart
  - mcp__qwen-patent-creator__create_block_diagram
  - mcp__qwen-patent-creator__add_diagram_references
  - mcp__qwen-patent-creator__get_diagram_templates
  - mcp__qwen-patent-creator__check_diagram_tools_status
  - mcp__qwen-patent-creator__review_epo_claims
  - mcp__qwen-patent-creator__review_epo_specification
  - mcp__qwen-patent-creator__check_epo_formalities
  - mcp__qwen-patent-creator__check_pct_formalities
  - mcp__qwen-patent-creator__check_epo_api_status
  - mcp__qwen-patent-creator__search_epo_patents
  - mcp__qwen-patent-creator__get_epo_patent
  - mcp__qwen-patent-creator__get_epo_patent_family
  - mcp__qwen-patent-creator__check_google_patents_status
  - mcp__qwen-patent-creator__search_patents_google
  - mcp__qwen-patent-creator__search_non_patent_literature
  - mcp__qwen-patent-creator__search_mpep
  - mcp__qwen-patent-creator__get_mpep_section
  - mcp__qwen-patent-creator__check_package
  - mcp__qwen-patent-creator__search_patent_law
  - mcp__qwen-patent-creator__search_uspto_api
  - mcp__qwen-patent-creator__get_uspto_patent
  - mcp__qwen-patent-creator__get_recent_uspto_patents
  - mcp__qwen-patent-creator__check_uspto_api_status
  - mcp__qwen-patent-creator__check_web_history
---

# Patent Development Agent

Load the `development-assistant` skill for executable API examples and the `testing-assistant` skill for test commands. Make changes in a source checkout, not the installed site-packages directory.

## Development patterns

1. Add tools to the appropriate module under `mcp_server/tools/`; register the group in `mcp_server/server.py`.
2. Define Pydantic inputs in `mcp_server/validation.py`. Call `validate_input(Model, field=value)` inside the function. It is not a decorator.
3. Register with `@mcp.tool()` and monitor with a named factory call such as `@track_performance("tool_name")`.
4. Analyzer subclasses implement both `analyze` and `_issue_to_dict`, reset collected issues between calls and describe only checks actually performed.
5. Add configuration through `Option` entries in `mcp_server/config.py`. Read effective values with `get_effective`.
6. Add distributed plugin commands to root `commands/`.
7. Add regression tests under `tests/`, run them against the checkout, and prove bug tests fail without the fix.
8. Use `mcp_server/cli_command.py` for Python-generated user commands.

## Repository map

```text
mcp_server/
  server.py                 MCPServer entry point and tool registration
  tools/                    MCP tool groups
  analyzer_base.py          Shared analyzer contract
  claims_analyzer.py        US claims checks
  specification_analyzer.py US specification checks
  formalities_checker.py    US formalities checks
  epo_claims_analyzer.py     EPO claims checks
  mpep_search.py            Legal-reference indexing and retrieval
  bigquery_search.py        Optional BigQuery patent searches
  config.py                 Saved settings and environment precedence
  data_paths.py             Platform storage locations
  hardware_detect.py        GPU detection and wheel selection
  monitoring.py             Timers and performance decorators
  logging_config.py         Structured logging
  validation.py             Input models and validation function
```

Use the **Testing Assistant** skill for local tests and optional service checks. SerpApi is the default patent-search provider; a missing BigQuery configuration must not block unrelated development or base-install tests. Record skipped checks and avoid claiming that a unit-test pass proves a live installation works.
