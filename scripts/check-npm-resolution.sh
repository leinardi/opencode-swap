#!/usr/bin/env bash
#
# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Verify the TUI plugin's npm payload installs the way OpenCode installs it.
#
# OpenCode installs npm TUI plugins itself, with @npmcli/arborist reify() and
# without legacyPeerDeps or force (packages/core/src/npm.ts). A peer conflict
# that Bun only warns about therefore makes the plugin uninstallable for every
# user, while `bun run check` still passes green — exactly how 0.4.0 shipped
# with a solid-js peer that no @opentui/solid 0.5.x release could satisfy.
#
# Resolve the packed payload with the Arborist version OpenCode pins, under
# Bun, which is what OpenCode runs on. The local `npm` CLI is not a stand-in:
# npm 12 installs 0.4.5's peers cleanly because it skips @opencode-ai/plugin's
# optional @opentui/keymap peer, while OpenCode's Arborist 9.4.0 pulls in
# keymap 0.6 and fails with ERESOLVE, so that package never installed.
set -euo pipefail

# OpenCode's packages/core/package.json pins this. Bump it when OpenCode does.
ARBORIST_VERSION="9.4.0"

PLUGIN_DIR="${1:?usage: check-npm-resolution.sh <plugin-dir>}"
PLUGIN_DIR="$(cd "$PLUGIN_DIR" && pwd)"

command -v npm >/dev/null 2>&1 || {
  echo "npm is required to pack the TUI plugin and fetch @npmcli/arborist." >&2
  exit 1
}
command -v bun >/dev/null 2>&1 || {
  echo "Bun is required to resolve the TUI plugin the way OpenCode does (OpenCode runs on Bun)." >&2
  exit 1
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[npm-resolve] packing $PLUGIN_DIR"
TARBALL="$(cd "$WORK_DIR" && npm pack "$PLUGIN_DIR" --ignore-scripts --silent)"

echo "[npm-resolve] fetching @npmcli/arborist@$ARBORIST_VERSION"
mkdir -p "$WORK_DIR/arborist"
echo '{"name":"arborist-host","version":"0.0.0","private":true}' >"$WORK_DIR/arborist/package.json"
if ! npm install "@npmcli/arborist@$ARBORIST_VERSION" \
  --prefix "$WORK_DIR/arborist" \
  --ignore-scripts \
  --no-audit \
  --no-fund \
  >"$WORK_DIR/arborist.log" 2>&1; then
  cat "$WORK_DIR/arborist.log" >&2
  exit 1
fi

# A bare scratch project, so the tarball's peers resolve from the registry as
# they would for a first-time user, with no lockfile to pin around them.
mkdir -p "$WORK_DIR/probe"
echo '{"name":"npm-resolution-probe","version":"0.0.0","private":true}' >"$WORK_DIR/probe/package.json"

# Same Arborist options as OpenCode's reify(). buildIdealTree() is the step of
# reify() that raises ERESOLVE, so it checks resolution without downloading
# every tarball. The throwaway cache keeps a stale ~/.npm packument from
# hiding a conflict the registry's current versions would cause.
cat >"$WORK_DIR/resolve.mjs" <<'EOF'
import { createRequire } from "node:module"

const [arboristDir, probeDir, cacheDir, tarball] = process.argv.slice(2)
const Arborist = createRequire(`${arboristDir}/package.json`)("@npmcli/arborist")
const arborist = new Arborist({
  path: probeDir,
  cache: cacheDir,
  binLinks: true,
  progress: false,
  savePrefix: "",
  ignoreScripts: true,
})
const label = (node) => (node ? `${node.name}@${node.version}` : "?")
try {
  await arborist.buildIdealTree({ add: [tarball], saveType: "prod" })
} catch (error) {
  console.error(`${error.code ?? "error"}: ${error.message}`)
  if (error.edge) {
    console.error(`  ${label(error.edge.from)} wants ${error.edge.name}@"${error.edge.spec}" (${error.edge.type})`)
  }
  if (error.current) console.error(`  but the tree already has ${label(error.current)}`)
  process.exit(1)
}
EOF

echo "[npm-resolve] resolving peer dependency tree with Arborist $ARBORIST_VERSION"
if ! bun "$WORK_DIR/resolve.mjs" \
  "$WORK_DIR/arborist" "$WORK_DIR/probe" "$WORK_DIR/cache" "$WORK_DIR/$TARBALL" \
  >"$WORK_DIR/resolve.log" 2>&1; then
  echo "" >&2
  echo "OpenCode's installer could not resolve the TUI plugin's dependency tree," >&2
  echo "so this package would fail to install for every user." >&2
  echo "" >&2
  cat "$WORK_DIR/resolve.log" >&2
  exit 1
fi

echo "[npm-resolve] OK"
