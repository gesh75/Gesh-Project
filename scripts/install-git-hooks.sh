#!/usr/bin/env bash
# Install repo git hooks (symlink so updates track the committed versions).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="$ROOT/.git/hooks"
mkdir -p "$HOOKS_DIR"
for hook in "$ROOT"/scripts/hooks/*; do
  name="$(basename "$hook")"
  ln -sf "../../scripts/hooks/$name" "$HOOKS_DIR/$name"
  chmod +x "$hook"
  echo "installed $name hook"
done
echo "done. Bypass a single push (emergency only) with: git push --no-verify"
