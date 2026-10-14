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
# Verify the packed TUI plugin is a loadable, precompiled OpenCode 2 plugin.
#
# OpenCode's TUI compiles .tsx through @opentui/solid's Bun transform plugin,
# whose source filter deliberately excludes node_modules paths: published
# packages are expected to ship precompiled code. A package that points at raw
# JSX loads through Bun's generic JSX runtime instead of Solid's compile-time
# transform - it renders once with no reactivity wired at all, so the widget
# freezes silently while commands keep working. That is exactly how 0.4.1
# shipped: broken from npm, working from a checkout, with no error anywhere.
# OpenCode 2 also rejects a plugin-local copy of `effect` or `@opencode/*`
# ("plugins must use the host's ..."), treats a "." export as a server plugin,
# and needs a default export with a string `id` and a function `setup`
# (docs/opencode-v2-integration.md, V4/V5). Typecheck, lint and npm
# resolution are blind to all of it, so inspect the real packed payload.
set -euo pipefail

PLUGIN_DIR="${1:?usage: check-tui-entry-compiled.sh <plugin-dir>}"
PLUGIN_DIR="$(cd "$PLUGIN_DIR" && pwd)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

fail() {
  echo "[tui-entry] $*" >&2
  exit 1
}

echo "[tui-entry] packing $PLUGIN_DIR"
TARBALL="$(cd "$WORK_DIR" && npm pack "$PLUGIN_DIR" --ignore-scripts --silent)"
tar -xzf "$WORK_DIR/$TARBALL" -C "$WORK_DIR"
PKG="$WORK_DIR/package"

EXPORTS="$(node -p "Object.keys(require('$PKG/package.json').exports ?? {}).join(' ')")"
[ "$EXPORTS" = "./tui" ] || fail "package.json exports must be exactly ./tui, got: ${EXPORTS:-none}"

ENTRY="$(node -p "require('$PKG/package.json').exports['./tui']")"
echo "[tui-entry] entry: $ENTRY"
case "$ENTRY" in
*.js) ;;
*) fail "entry must be compiled .js, got: $ENTRY (OpenCode never runs the Solid transform on node_modules paths)" ;;
esac
ENTRY_FILE="$PKG/${ENTRY#./}"
[ -f "$ENTRY_FILE" ] || fail "entry file $ENTRY is not part of the packed payload (missing from 'files'?)"

mapfile -t FILES < <(find "$PKG" -type f \( -name '*.js' -o -name '*.mjs' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) | sort)
for file in "${FILES[@]}"; do
  case "$file" in
  *.js | *.mjs) ;;
  *) fail "${file#"$PKG"/} is not compiled JavaScript" ;;
  esac
done

# Solid's universal-renderer output imports its runtime helpers from
# @opentui/solid; the generic JSX fallback imports jsx-runtime instead. Only
# JSX sources compile to those helpers: the discontinuation notice has none.
if find "$PLUGIN_DIR/src" -name '*.tsx' -print -quit | grep -q .; then
  grep -q 'createComponent' "$ENTRY_FILE" || fail "entry lacks Solid compile markers (createComponent) - not precompiled Solid output"
fi
if grep -l 'jsx-runtime\|jsx-dev-runtime\|jsxDEV' "${FILES[@]}"; then
  fail "the files above use the generic JSX runtime - Solid reactivity would not be wired"
fi
if grep -nE "(from|import|require)[[:space:]]*\(?[[:space:]]*[\"'](effect|@opencode/)" "${FILES[@]}"; then
  fail "runtime import of effect or @opencode/* above - OpenCode refuses a plugin-local copy; import types only"
fi

# The build adds `.js` to every relative specifier: Bun would resolve one
# without it, but a bare ESM loader would not.
if grep -nE "(from|import)[[:space:]]*\(?[[:space:]]*[\"']\.\.?/" "${FILES[@]}" | grep -vE "\.js[\"']"; then
  fail "relative imports above lack their .js extension"
fi

# Importing the entry also proves every relative import resolves inside the
# payload. The peers come from the development install, as the host provides
# them at runtime.
ln -s "$PLUGIN_DIR/node_modules" "$PKG/node_modules"
SHAPE="$(cd "$PKG" && bun -e "const m = await import('$ENTRY_FILE'); console.log(typeof m.default?.id === 'string' && m.default.id.length > 0 && typeof m.default?.setup === 'function' ? 'ok' : 'bad')" 2>&1)" ||
  fail "importing the entry failed: $SHAPE"
[ "$SHAPE" = "ok" ] || fail "the entry's default export must have a string id and a function setup"

echo "[tui-entry] OK"
