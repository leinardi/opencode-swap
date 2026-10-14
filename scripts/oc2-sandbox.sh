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
# An isolated OpenCode install for integration work, with fake credentials only.
#
# OpenCode resolves every data, config, state and cache path from XDG_* and HOME
# (and writes logs even for `--version`: running the bare binary once during the
# OpenCode 2 investigation appended to the real ~/.local/share/opencode log). So
# the binary is never put on PATH: every invocation goes through `run`/`tui`,
# which start it with a cleared environment (no inherited provider keys) whose
# HOME, XDG_* and TMPDIR point into the sandbox, and `check` proves the real
# OpenCode directories were not modified since the sandbox was created.
#
# Usage: scripts/oc2-sandbox.sh [--v1] [--version VER] COMMAND [ARGS...]
#
#   install      npm-install the pinned OpenCode into the sandbox prefix
#   bin          print the explicit binary path
#   run ARGS     run OpenCode with the sandbox environment
#   tui [ARGS]   start the TUI in a detached tmux session
#   capture      print the TUI's tmux pane
#   keys KEYS    send tmux keys to the TUI (tmux send-keys syntax)
#   stop         kill the TUI's tmux session and stop the background service
#   check        fail if a real OpenCode directory changed since the sandbox was created
#   reset        delete the sandbox state (keeps the install)
#
# --v1 selects OpenCode 1.19.0 (npm opencode-ai) in /tmp/oc1 instead of
# OpenCode 2 (npm @opencode/cli) in /tmp/oc2. OC_SANDBOX_DIR overrides the
# prefix; OC_SANDBOX_VERSION (or --version) the version; OC_SANDBOX_REGISTRY
# (e.g. a local verdaccio at http://127.0.0.1:4873/) the npm registry that
# OpenCode installs plugins from. OpenCode runs with a cleared environment;
# OC_SANDBOX_PASS names extra variables to pass through (fake values only).

set -euo pipefail

variant=2
version=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --v1) variant=1; shift ;;
    --version) version="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "oc2-sandbox: unknown option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

if [[ "$variant" == 1 ]]; then
  package=opencode-ai
  platform_prefix=opencode
  default_version=1.19.0
  default_dir=/tmp/oc1
else
  package=@opencode/cli
  platform_prefix=@opencode/cli
  default_version=2.0.29
  default_dir=/tmp/oc2
fi
version="${version:-${OC_SANDBOX_VERSION:-$default_version}}"
prefix="${OC_SANDBOX_DIR:-$default_dir}"
state="$prefix/state"
marker="$state/.sandbox-marker"
session="$(basename "$prefix")-sandbox"

# Both packages ship the real binary in a per-platform optional dependency and
# only link it into bin/ from a postinstall script, which npm >= 11 blocks unless
# allow-listed. Using the platform package's binary directly needs no scripts.
binary_path() {
  local os arch
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) echo "oc2-sandbox: unsupported OS $(uname -s)" >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) echo "oc2-sandbox: unsupported arch $(uname -m)" >&2; return 1 ;;
  esac
  local candidate="$prefix/node_modules/$platform_prefix-$os-$arch/bin/opencode"
  if [[ ! -x "$candidate" ]]; then
    echo "oc2-sandbox: $candidate missing; run: $0 $([[ $variant == 1 ]] && echo '--v1 ')install" >&2
    return 1
  fi
  printf '%s\n' "$candidate"
}

real_dirs() {
  local base
  for base in "${XDG_DATA_HOME:-$HOME/.local/share}" "${XDG_CONFIG_HOME:-$HOME/.config}" \
    "${XDG_STATE_HOME:-$HOME/.local/state}" "${XDG_CACHE_HOME:-$HOME/.cache}"; do
    printf '%s\n' "$base/opencode" "$base/opencode2"
  done
}

ensure_state() {
  if [[ ! -e "$marker" ]]; then
    mkdir -p "$state"/{home,data,config,state,cache,tmp}
    touch "$marker"
  fi
}

# The complete environment of every sandboxed OpenCode process, passed through
# `env -i`. An allowlist rather than a denylist of provider keys: OpenCode turns
# any provider key in its server's environment into an env connection (see
# docs/opencode-v2-integration.md, V8), and models.dev knows far more key names
# than a denylist would keep up with.
sandbox_env() {
  local name
  # OC_SANDBOX_PASS deliberately passes named variables through, e.g. a fake
  # key to observe an env connection.
  # shellcheck disable=SC2086 # word splitting of the name list is intended
  for name in PATH TERM COLORTERM LANG LC_ALL LC_CTYPE USER LOGNAME SHELL ${OC_SANDBOX_PASS:-}; do
    if [[ -n "${!name:-}" ]]; then
      printf '%s=%s\n' "$name" "${!name}"
    fi
  done
  printf '%s\n' \
    "HOME=$state/home" \
    "XDG_DATA_HOME=$state/data" \
    "XDG_CONFIG_HOME=$state/config" \
    "XDG_STATE_HOME=$state/state" \
    "XDG_CACHE_HOME=$state/cache" \
    "TMPDIR=$state/tmp"
  # OpenCode installs plugins with Arborist using the npm config it loads from
  # the environment and ~/.npmrc (@npmcli/config), so this points plugin
  # installs at a local registry (verdaccio) holding unpublished test builds.
  if [[ -n "${OC_SANDBOX_REGISTRY:-}" ]]; then
    printf '%s\n' "npm_config_registry=$OC_SANDBOX_REGISTRY"
  fi
}

run_sandboxed() {
  local bin
  bin="$(binary_path)"
  ensure_state
  local -a env_args
  mapfile -t env_args < <(sandbox_env)
  env -i "${env_args[@]}" "$bin" "$@"
}

command="${1:-}"
[[ $# -gt 0 ]] && shift
case "$command" in
  install)
    mkdir -p "$prefix"
    npm install --prefix "$prefix" --no-audit --no-fund --ignore-scripts "$package@$version"
    ensure_state
    echo "binary: $(binary_path)"
    ;;
  bin)
    binary_path
    ;;
  run)
    run_sandboxed "$@"
    ;;
  tui)
    bin="$(binary_path)"
    ensure_state
    mapfile -t env_args < <(sandbox_env)
    # The pane is tmux's terminal, not the caller's; env keeps the last TERM.
    env_args+=("TERM=$(tmux show-options -gv default-terminal 2>/dev/null || echo tmux-256color)")
    tmux kill-session -t "$session" 2>/dev/null || true
    # env -i also drops the tmux server's global environment, which the pane
    # would otherwise inherit.
    tmux new-session -d -s "$session" -x 160 -y 48 \
      env -i "${env_args[@]}" "$bin" "$@"
    echo "tmux session: $session (binary: $bin)"
    ;;
  capture)
    tmux capture-pane -p -t "$session"
    ;;
  keys)
    tmux send-keys -t "$session" "$@"
    ;;
  stop)
    tmux kill-session -t "$session" 2>/dev/null || true
    # The TUI and every CLI command without --standalone start a managed
    # background service on a fixed port (49374 by default), which outlives the
    # TUI and blocks another sandbox's service from starting.
    if [[ "$variant" == 2 ]]; then
      run_sandboxed service stop </dev/null >/dev/null 2>&1 || true
    fi
    ;;
  check)
    if [[ ! -e "$marker" ]]; then
      echo "oc2-sandbox: no sandbox marker at $marker" >&2
      exit 1
    fi
    changed=0
    while IFS= read -r dir; do
      [[ -e "$dir" ]] || continue
      # Count only: the real directories hold real credentials, so no names or
      # contents are printed. An unreadable subdirectory must not abort the
      # check silently under pipefail.
      count="$({ find "$dir" -newer "$marker" 2>/dev/null || true; } | wc -l)"
      if [[ "$count" -gt 0 ]]; then
        echo "CHANGED: $dir ($count entries newer than the sandbox marker)" >&2
        changed=1
      fi
    done < <(real_dirs)
    if [[ "$changed" -ne 0 ]]; then
      echo "oc2-sandbox: a real OpenCode directory changed; a concurrently running real OpenCode also does this, so stop it before trusting the check" >&2
      exit 1
    fi
    echo "oc2-sandbox: real OpenCode directories untouched since $(date -r "$marker" '+%F %T')"
    ;;
  reset)
    tmux kill-session -t "$session" 2>/dev/null || true
    if [[ "$variant" == 2 && -e "$marker" ]]; then
      run_sandboxed service stop </dev/null >/dev/null 2>&1 || true
    fi
    rm -rf "$state"
    ensure_state
    echo "oc2-sandbox: state reset at $state"
    ;;
  *)
    sed -n '/^# Usage:/,/^# OC_SANDBOX_PASS names/p' "$0" | sed 's/^# \{0,1\}//'
    [[ -z "$command" ]] && exit 0
    exit 2
    ;;
esac
