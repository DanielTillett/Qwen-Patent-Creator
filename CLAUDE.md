# Claude Code

This fork keeps its project instructions in [AGENTS.md](AGENTS.md), the file the other clients read. Claude Code
reads `CLAUDE.md` instead, so this file points at the same content rather than keeping a second copy that can drift.

The fork ships no Claude plugin layer: Claude Code gets the MCP tools, and the skills, agents and slash commands are
generated for OpenCode and Qwen Code. Use upstream for the Claude Code plugin.
