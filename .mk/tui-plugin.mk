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

ifndef MK_LOCAL_TUI_PLUGIN_INCLUDED
MK_LOCAL_TUI_PLUGIN_INCLUDED := 1

BUN ?= bun
TUI_PLUGIN_DIR := $(REPO_ROOT)/integrations/opencode-tui-plugin

.PHONY: tui-plugin-require-bun
tui-plugin-require-bun:
	@$(BUN) --version >/dev/null 2>&1 || { echo "Bun 1.3.14 is required for TUI plugin verification. Install Bun, then rerun make tui-plugin-sync." >&2; exit 1; }

.PHONY: tui-plugin-sync
tui-plugin-sync: tui-plugin-require-bun ## Install locked OpenCode TUI plugin typecheck dependencies
	$(BUN) install --cwd $(TUI_PLUGIN_DIR) --frozen-lockfile

.PHONY: tui-plugin-typecheck
tui-plugin-typecheck: tui-plugin-sync ## Typecheck OpenCode TUI plugin
	$(BUN) run --cwd $(TUI_PLUGIN_DIR) typecheck

.PHONY: tui-plugin-lint
tui-plugin-lint: tui-plugin-sync ## Lint OpenCode TUI plugin
	$(BUN) run --cwd $(TUI_PLUGIN_DIR) lint

.PHONY: tui-plugin-build
tui-plugin-build: tui-plugin-sync ## Compile OpenCode TUI plugin entry with the Solid transform
	$(BUN) run --cwd $(TUI_PLUGIN_DIR) build

.PHONY: tui-plugin-package-check
tui-plugin-package-check: tui-plugin-build ## Verify OpenCode TUI plugin npm payload
	$(BUN) run --cwd $(TUI_PLUGIN_DIR) pack:check

# OpenCode's Solid transform skips node_modules, so the published entry must
# already be compiled; raw JSX ships silently and renders without reactivity.
.PHONY: tui-plugin-entry-check
tui-plugin-entry-check: tui-plugin-build ## Verify packed TUI plugin entry is precompiled Solid output
	"$(REPO_ROOT)/scripts/check-tui-entry-compiled.sh" "$(TUI_PLUGIN_DIR)"

# Deliberately does not depend on tui-plugin-sync: this must resolve the packed
# payload with npm from a clean slate, because Bun only warns on the peer
# conflicts that make OpenCode's npm-based plugin install fail outright.
.PHONY: tui-plugin-npm-resolve-check
tui-plugin-npm-resolve-check: ## Verify npm can resolve OpenCode TUI plugin peer dependencies
	"$(REPO_ROOT)/scripts/check-npm-resolution.sh" "$(TUI_PLUGIN_DIR)"

endif  # MK_LOCAL_TUI_PLUGIN_INCLUDED
