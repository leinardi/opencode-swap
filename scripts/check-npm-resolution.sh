#!/usr/bin/env bash
# Verify the TUI plugin's npm payload installs under npm's strict peer rules.
#
# OpenCode installs npm TUI plugins itself, with @npmcli/arborist reify() and
# without legacyPeerDeps or force (packages/core/src/npm.ts). A peer conflict
# that Bun only warns about therefore makes the plugin uninstallable for every
# user, while `bun run check` still passes green — exactly how 0.4.0 shipped
# with a solid-js peer that no @opentui/solid 0.5.x release could satisfy.
#
# Bun cannot catch this class of bug, so pack the real payload and resolve it
# with npm the same way OpenCode will.
set -euo pipefail

PLUGIN_DIR="${1:?usage: check-npm-resolution.sh <plugin-dir>}"
PLUGIN_DIR="$(cd "$PLUGIN_DIR" && pwd)"

command -v npm >/dev/null 2>&1 || {
  echo "npm is required to verify npm peer resolution (OpenCode installs plugins with npm)." >&2
  exit 1
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[npm-resolve] packing $PLUGIN_DIR"
TARBALL="$(cd "$WORK_DIR" && npm pack "$PLUGIN_DIR" --ignore-scripts --silent)"

# A bare scratch project so npm resolves the tarball's peers from the registry
# exactly as it would for a real consumer, with no lockfile to pin around them.
echo '{"name":"npm-resolution-probe","version":"0.0.0","private":true}' >"$WORK_DIR/package.json"

echo "[npm-resolve] resolving peer dependency tree"
if ! npm install "$WORK_DIR/$TARBALL" \
  --prefix "$WORK_DIR" \
  --dry-run \
  --ignore-scripts \
  --no-audit \
  --no-fund \
  >"$WORK_DIR/npm.log" 2>&1; then
  echo "" >&2
  echo "npm could not resolve the TUI plugin's dependency tree." >&2
  echo "OpenCode installs plugins with npm, so this package would fail to install for every user." >&2
  echo "" >&2
  cat "$WORK_DIR/npm.log" >&2
  exit 1
fi

echo "[npm-resolve] OK"
