# Qwen Patent Creator — adaptation plan

This fork adapts [Claude-Patent-Creator](https://github.com/RobThePCGuy/Claude-Patent-Creator) to run with Qwen models. Reviewing the code shows **the MCP server never calls a Claude model at runtime**: all search, analysis and diagram tools are deterministic code plus open-source embedding/reranker models. Claude appears in three places only — the optional HyDE API backend, the setup-time client registration, and the Claude Code plugin layer. The plan follows that split.

Inventory (measured at the fork point): 17 skills, 13 agents, 15 commands, `hooks/hooks.json`, `.mcp.json`,
`CLAUDE.md` (now `AGENTS.md`). `.claude-plugin/plugin.json` + `marketplace.json` were present at the fork point and
are removed by this plan (§5).

## 1. What does not change (model-agnostic core)

- `mcp_server/` RAG search (FAISS + BM25 + cross-encoder reranker) — open-source models (BGE-base-en-v1.5, MS-MARCO MiniLM).
- Claims/specification/formalities checkers, diagram generation, BigQuery/SerpApi/EPO OPS/USPTO/paper search — deterministic, no LLM calls.
- Any MCP-compatible client running Qwen can already use the server.

One caveat: `mcp_server/data_paths.py` hardcodes `_APP_DIR_NAME = "claude-patent-creator"`, so a co-installed fork shares the corpus, index and diagrams with upstream. That is an asset-sharing benefit (no second ~650 MB download) but also a coupling — see §5.

## 2. HyDE backend (`mcp_server/hyde.py`)

HyDE is opt-in (`HYDE_BACKEND=api` plus `PATENT_MPEP_USE_HYDE=true`) and is the only code that calls an LLM API.

- Add a `qwen` provider branch. Qwen speaks the OpenAI protocol, so the OpenAI SDK is reused with a base URL.
- **Keys are read per provider, never forwarded.** An `OPENAI_API_KEY` is not sent to a Qwen endpoint; `auto` with only an OpenAI key means the OpenAI API. (The earlier idea of `QWEN_API_KEY` → `OPENAI_API_KEY` fallback is dropped: it would send patent queries to OpenAI under a name that says Qwen.)
- Provider selection: `HYDE_API_PROVIDER=auto|qwen|openai|anthropic`. `auto` picks the first provider that has its own key, in the order Qwen → OpenAI → Anthropic. An unknown value raises rather than defaulting.
- Base URL: `HYDE_OPENAI_BASE_URL` (or `OPENAI_BASE_URL`) overrides; the default for the Qwen provider is DashScope compatible-mode `https://dashscope.aliyuncs.com/compatible-mode/v1`. Point it at a local vLLM/Ollama serve for fully local HyDE — those accept a placeholder key, so the existing "no key → rule-based" detection cannot distinguish "user wants local" from "no key", and the error message says so.
- Model: `HYDE_QWEN_MODEL`, default `qwen3-coder-flash`. HyDE is one short generation per search, so the cheap tier is the sensible default; `qwen3-coder-plus` costs roughly double on DashScope (~$0.30/$1.50 vs ~$0.50/$1.50 per million tokens, and roughly double again for a coding-plan key). Kept as an env override, not a hard default.
- Strict mode: `HYDE_STRICT=true` makes an API failure raise instead of silently degrading to rule-based, so users who asked for Qwen get a real error. Default behaviour stays soft-fallback.
- Confidentiality note for docs and the skill: enabling HyDE sends raw query text — which can contain invention detail — to the configured endpoint. DashScope is a third-party API; that is not the same as "works offline".

## 3. Setup-time client registration (`mcp_server/cli.py`)

- `--client claude|opencode|qwen-code|auto|none`, plus `--scope user|project`. Today `configure_mcp_server()` hardcodes Claude Code and `--scope user` (two call sites, `cli.py:418` and `cli.py:478`).
- `--client none` must skip registration *and* let setup succeed, with an honest summary and a correct exit status (currently the setup path returns 1 on a failed registration; "not configured, nothing to verify" must not be reported as failure).
- `verify_config_command` (`cli.py:1203`) and `_resolve_w2x_cli()` (`cli.py:594-595`, which returns `'claude'` unconditionally, and `cli.py:158` which prints Claude-specific instructions) need to follow the selected client.
- Config shapes differ, so registration is per-client:
  - Claude Code: `claude mcp add` / `~/.claude.json`, existing behaviour preserved.
  - OpenCode (V2): `mcp.servers.<name>` with `type: "local"`, `command` as an array, optional `environment`. V1 wrote server names directly under `mcp`; both shapes must be read, V2 is written. File: `opencode.json`/`opencode.jsonc`, project or global (`~/.config/opencode/`).
  - Qwen Code: `mcpServers.<name>`, `.qwen/settings.json`.
  - Existing entries and comments (JSONC) are preserved: read, merge, write; never truncate a user's config.
- Server name is `<name>`, which becomes the tool prefix `<name>_<tool>` in OpenCode. Use `qwen-patent-creator` so it cannot collide with an upstream server already registered under `patent-creator`.
- Handshake test wording ("Testing the connection the way Claude Code will...") becomes client-neutral.

## 4. Plugin layer → Qwen clients

The plugin layer is plain Markdown prompts, so porting is format mapping, not rewriting content.

### OpenCode (primary target)

- **Agents** → `.opencode/agents/<name>.md` (project) or `~/.config/opencode/agents/<name>.md` (global). Body is the system prompt. Frontmatter keeps `description`; add `mode: subagent`; `tools:` becomes `permissions` rules.
- **Commands** → `.opencode/commands/<name>.md` or `~/.config/opencode/commands/`. Claude's `$ARGUMENTS` is OpenCode's `$ARGUMENTS` — compatible. Nested paths become `/team/review`; legacy `command/` is still discovered, but new files use `commands/`.
- **Parameter syntax is not shared across clients**: OpenCode and Claude use `$ARGUMENTS`, Qwen Code uses `{{args}}` (TOML commands are deprecated). A single `commands/` tree cannot serve both, so commands are generated per client target rather than duplicated by hand.
- **Skills** → `.opencode/skills/<id>/SKILL.md`; the whole skill directory is copied so relative paths keep working. No body rewrite is needed beyond `${CLAUDE_PLUGIN_ROOT}` references, which become MCP tool calls or paths relative to `SKILL.md`. Skill ID comes from the directory name, so directory names must be valid IDs; `description` is required for the skill to be advertised.
- **Permissions**: canonical `tools:` maps to OpenCode `permissions` rules with actions `shell`/`edit`/`subagent`/`skill`/`webfetch`/`websearch`/`read`/`glob`/`grep`. An omitted `tools:` means the agent inherits its default permissions rather than an allow-all list.
- **Hooks**: OpenCode has no equivalent for `hooks/hooks.json`; documented as unsupported. Hooks are ported to Qwen Code `settings.json`.
- Subagents run in background child sessions and inherit the parent model unless a model is configured, so pinning a model is left to the user's client config; the canonical source carries no `model:` field (see §5 layout rules).

### Qwen Code CLI (secondary target)

- Agents → `.qwen/agents/<name>.md` + `~/.qwen/agents/`; frontmatter `name`, `description`, `model` (`inherit`/`fast`/`modelId`/`authType:modelId`), `approvalMode`, `tools`, `disallowedTools`.
- Commands → `.qwen/commands/<name>.md` with `description` frontmatter and `{{args}}` (TOML commands are deprecated).
- Skills → `.qwen/skills/<name>/SKILL.md` (also `~/.qwen/skills/` and `.agents/skills/`); frontmatter `name`, `description`, optional `priority`, `paths`, `user-invocable`, `disable-model-invocation`, `allowedTools`. An `allowed-tools` field written for another agent is ignored.
- Hooks → `.qwen/settings.json` `hooks` block, generated from `hooks/hooks.json`. Events and matcher shape match the Claude file (`SessionStart`, `PreToolUse`, `PostToolUse`, `type: command`, regex matcher, Claude tool names accepted as aliases). Project-level hooks need folder trust or explicit approval, and `disableAllHooks`/`security.folderTrust` changes require a restart. Hooks receive JSON on stdin; plain-text stdout on `SessionStart` is added to model context, exit code 2 blocks with stderr.

### Canonical source and generator

Layout (single source of truth at the repo root, committed per-client output):

```text
skills/<name>/SKILL.md            canonical
agents/<name>.md                  canonical
commands/<name>.md                canonical
hooks/hooks.json                  canonical hook definitions

.opencode/skills|agents|commands  generated for OpenCode (primary)
.qwen/skills|agents|commands      generated for Qwen Code CLI (secondary)
.qwen/settings.json               hooks, generated from hooks/hooks.json
```

`scripts/build_plugin_targets.py` emits the trees; output must be byte-stable (sorted frontmatter keys,
sorted files) and a parity test fails if the committed trees drift from the canonical source.

Canonical frontmatter rules:

- One `tools:` field only, using a neutral vocabulary (`read`, `write`, `edit`, `glob`, `grep`, `bash`,
  `webfetch`, `websearch`, `subagent`, `skill`, `todowrite`); `allowed-tools` is not used.
- No `model:` field: the current canonical values are Claude aliases (`sonnet`, `opus`, `haiku`) and a pinned
  `claude-sonnet-4-5-20250929`, which are meaningless to Qwen clients and invalid as OpenCode
  `provider/model` values.
- `description`, `argument-hint`, `priority`, `user-invocable`, `disable-model-invocation` are portable.
- Prose tool-name mentions are mapped only when they appear as backticked single tokens (16 today), so
  narrative text is not rewritten unpredictably.

Per-target mapping:

| Canonical | OpenCode | Qwen Code |
|---|---|---|
| agent `tools` | `permissions` rules | `tools` string |
| skill `tools` | not emitted | `allowedTools` list |
| tool names | `read`/`glob`/`grep`/`edit`/`shell`/`webfetch`/`websearch`/`subagent`/`skill` | `read_file`/`glob`/`grep_search`/`edit`/`run_shell_command`/`web_fetch`/`web_search`/`agent`/`skill`/`todo_write` |
| arguments | `$ARGUMENTS` | `{{args}}` |

Two further rules the generator implements:

- **MCP tools are qualified per client.** Qwen Code names them `mcp__qwen-patent-creator__<tool>`; an OpenCode
  permission action is `<server>_<tool>` with non-word characters turned into `_`, so
  `qwen_patent_creator_<tool>`. A bare tool name does not match an MCP tool.
- **OpenCode permissions are appended to an allow-all default policy, and the last matching rule wins.** A
  canonical `tools:` allowlist therefore needs a leading `{action: "*", resource: "*", effect: deny}` rule before
  the per-tool allows. `write` and `edit` map to the same action, so duplicate rules are skipped; `todowrite` has no
  OpenCode action and is omitted. When an agent may read or write, an `external_directory` allow keeps the default
  allowance for the data directory outside the project.

## 5. Naming and packaging (blocks publishing)

`pyproject.toml` is the shipped identity: `name = "claude-patent-creator"`, `[project.scripts]` `patent-creator` and `claude-patent-creator` → `mcp_server.cli:main`; `mcp_server/_version.py:19` `PACKAGE_NAME = "claude-patent-creator"`; `setup.py` hardcoded the package name. `MANIFEST.in` does not include the plugin trees, so a `pip install` ships the MCP server only and the generated workflows exist in a source checkout.

**Decided (applied):** fork-only identity. Distribution name and console script are `qwen-patent-creator`, and the
MCP server name in client config is `qwen-patent-creator` so it does not collide with an upstream entry. `mcp_server`
stays the import package, which means the fork and upstream cannot be installed in the same environment — documented
rather than solved, since giving the fork its own top-level package is a larger change than a rename. The
`patent-creator` alias is dropped so it does not shadow upstream's. `_version.py` reads `PACKAGE_NAME` from installed
metadata, so the name must match the distribution, and it now reports `qwen-patent-creator`.

**Decided (applied): the Claude plugin layer is dropped.** `.claude-plugin/plugin.json` and `marketplace.json` are
removed; the fork serves Claude Code through the MCP tools only, and anyone wanting the Claude plugin uses upstream.
This is why canonical frontmatter must be client-neutral: the generator, not the plugin manifest, is what produces
per-client tool lists and models.

**Decided (applied): the fork keeps its own data and config folder** (`qwen-patent-creator` app-data/config dir, from
`APP_DIR_NAME` in `mcp_server/config.py` and `data_paths.py`). An existing upstream install does not share the corpus,
so the first run downloads about 500 MB again; `PATENT_DATA_DIR` can point at an existing copy.

## 6. Acceptance criteria (observable, not "1:1 parity")

1. Clean install from an exact revision in a fresh venv; `qwen-patent-creator --version` reports the fork version.
2. Cold `setup --client none --non-interactive` with an isolated data dir completes with exit 0 and writes nothing to a client config.
3. `setup --client opencode --scope project` and `--scope user` write the expected entry into an existing
   `opencode.jsonc` without losing existing keys or comments. Which entry a run sees is client runtime
   behaviour: the project file the project scope writes is checked by tests, and that a project entry wins
   over the global one is a manual check in a real project.
4. OpenCode lists the server in `opencode mcp list` as connected, and its tools appear as
   `qwen-patent-creator_*`; Qwen Code lists it under its MCP commands. `manual`: needs an installed client.
   Tests check the names those listings would show — the server name and the tool names as the generator
   and registration use them.
5. A Qwen-run agent invocation produces the same checker output as the Claude-run one on a fixed fixture,
   and an agent that needs a denied tool fails with a permission error naming the tool. `manual`: needs a live
   agent run. Tests check why it holds — the checkers read no client or model configuration, so the same
   fixture gives the same verdict in any client, and the generated policy denies a tool the agent does not list.
6. HyDE: `HYDE_API_PROVIDER=qwen` with `QWEN_API_KEY` resolves to the qwen provider and DashScope base URL;
   `HYDE_API_PROVIDER=openai` with only `OPENAI_API_KEY` uses OpenAI; `HYDE_STRICT=true` surfaces API failures;
   provider resolution is covered by tests without a live API key.
7. CI guards pass: package version bumped for installed-code changes, plugin version bumped for plugin-content
   changes, and the generated trees match the canonical source (`--check`).

`tests/test_acceptance.py` covers criteria 1-7 as far as a test run can observe them; the halves marked
`manual` need an installed client or a live agent run and are checked by hand.

## 7. Out of scope / decisions closed

- Patent Creator Studio (separate repo, hardcodes Claude Code and Codex) is out of scope.
- Naming: fork-only identity, applied (§5). `APP_DIR_NAME` is the fork's own folder, not shared with upstream (§5).
- Claude plugin layer: dropped (§5). The CI plugin-version guard now watches `skills/ agents/ commands/ hooks/
  .opencode/ .qwen/` and requires a bump in `pyproject.toml`, the file `mcp_server/_version.py` reads; the
  `plugin-trees-current` job runs `python scripts/build_plugin_targets.py --check`.
- Hooks: supported on Qwen Code (`settings.json`). OpenCode has no declarative hook configuration — its hooks are plugin code — so the generator
  writes `.qwen/settings.json` only and maps the matcher `Bash` → `run_shell_command`. Claude Code gets no hooks in
  this fork.
- HyDE verification is mocked in tests: no Qwen API key is available in CI, so provider resolution and strict mode
  are checked without a live call.
- `CLAUDE.md` is renamed to `AGENTS.md`, the file clients read for project instructions.
