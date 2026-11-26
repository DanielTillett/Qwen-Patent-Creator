#!/bin/bash
# Setup environment for a patent creator session.
#
# Only Claude Code hands a hook an environment file to append to (`$CLAUDE_ENV_FILE`);
# OpenCode and Qwen Code read what the hook prints, so each note is printed as well as
# written when a file is available.

note() {
  echo "$1"
  if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    echo "$1" >> "$CLAUDE_ENV_FILE"
  fi
}

# Add patent creator to PATH if installed
if command -v qwen-patent-creator &> /dev/null; then
  note "# Patent Creator CLI available"
fi

# Check for BigQuery credentials
if [ -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
  note "# BigQuery authenticated"
fi

echo "Patent Creator session initialized"
exit 0
