#!/usr/bin/env bash
# Verify the packed TUI plugin ships a precompiled Solid entrypoint.
#
# OpenCode's TUI compiles .tsx through @opentui/solid's Bun transform plugin,
# whose source filter deliberately excludes node_modules paths: published
# packages are expected to ship precompiled code. A package whose "./tui"
# export points at raw JSX therefore loads through Bun's generic JSX runtime
# instead of Solid's compile-time transform - it renders once with no
# reactivity wired at all, so the widget freezes silently while commands keep
# working. That is exactly how 0.4.1 shipped: broken from npm, working from a
# checkout, with no error anywhere. Typecheck, lint, and npm resolution are
# all blind to it, so inspect the real packed payload.
set -euo pipefail

PLUGIN_DIR="${1:?usage: check-tui-entry-compiled.sh <plugin-dir>}"
PLUGIN_DIR="$(cd "$PLUGIN_DIR" && pwd)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[tui-entry] packing $PLUGIN_DIR"
TARBALL="$(cd "$WORK_DIR" && npm pack "$PLUGIN_DIR" --ignore-scripts --silent)"
tar -xzf "$WORK_DIR/$TARBALL" -C "$WORK_DIR"
PKG="$WORK_DIR/package"

ENTRY="$(node -p "require('$PKG/package.json').exports['./tui'] ?? ''")"
if [ -z "$ENTRY" ]; then
  echo "[tui-entry] package.json exports['./tui'] is missing" >&2
  exit 1
fi
echo "[tui-entry] entry: $ENTRY"

case "$ENTRY" in
*.js) ;;
*)
  echo "[tui-entry] entry must be compiled .js, got: $ENTRY" >&2
  echo "[tui-entry] OpenCode never runs the Solid transform on node_modules paths; raw JSX/TSX renders without reactivity." >&2
  exit 1
  ;;
esac

ENTRY_FILE="$PKG/${ENTRY#./}"
if [ ! -f "$ENTRY_FILE" ]; then
  echo "[tui-entry] entry file $ENTRY is not part of the packed payload (missing from 'files'?)" >&2
  exit 1
fi

# Solid's universal-renderer output imports its runtime helpers from
# @opentui/solid; the generic JSX fallback imports jsx-runtime instead.
if ! grep -q 'createComponent' "$ENTRY_FILE"; then
  echo "[tui-entry] entry lacks Solid compile markers (createComponent) - not precompiled Solid output" >&2
  exit 1
fi
if grep -q 'jsx-runtime\|jsx-dev-runtime\|jsxDEV' "$ENTRY_FILE"; then
  echo "[tui-entry] entry uses the generic JSX runtime - Solid reactivity would not be wired" >&2
  exit 1
fi

echo "[tui-entry] OK"
