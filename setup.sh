#!/usr/bin/env bash
# Run once after cloning to configure git hooks.
set -euo pipefail

git config core.hooksPath .githooks
echo "Git hooks configured. Pre-commit will auto-bump the version in server.py."
