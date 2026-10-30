#!/bin/bash
# Install git hooks for bttco-app
# Run once after cloning: bash install-hooks.sh

HOOK_DIR="$(git rev-parse --git-dir)/hooks"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing pre-push hook..."
cp "$SCRIPT_DIR/hooks/pre-push" "$HOOK_DIR/pre-push"
chmod +x "$HOOK_DIR/pre-push"
echo "Done. Pre-push hook installed at $HOOK_DIR/pre-push"
