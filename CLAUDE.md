@AGENTS.md

# Claude Code

The project instructions live in `AGENTS.md`, which the `@` import above brings into this file so there is one copy
rather than two that can drift. Everything Claude Code needs to work in this repo is in that import.

The fork ships no Claude plugin layer: Claude Code gets the MCP tools, and the skills, agents and slash commands are
generated for OpenCode and Qwen Code. Use upstream for the Claude Code plugin.
