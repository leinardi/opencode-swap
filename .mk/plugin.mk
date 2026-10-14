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


ifndef MK_LOCAL_PLUGIN_INCLUDED
MK_LOCAL_PLUGIN_INCLUDED := 1

BUN ?= bun
# The plugin is the repository itself: package.json, src/ and test/ sit at the root.
PLUGIN_DIR := $(REPO_ROOT)

.PHONY: plugin-require-bun
plugin-require-bun:
	@$(BUN) --version >/dev/null 2>&1 || { echo "Bun 1.3.14 is required for plugin verification. Install Bun, then rerun make plugin-sync." >&2; exit 1; }

.PHONY: plugin-sync
plugin-sync: plugin-require-bun ## Install the plugin's locked dependencies
	$(BUN) install --cwd "$(PLUGIN_DIR)" --frozen-lockfile

.PHONY: plugin-typecheck
plugin-typecheck: plugin-sync ## Typecheck the plugin
	$(BUN) run --cwd "$(PLUGIN_DIR)" typecheck

.PHONY: plugin-lint
plugin-lint: plugin-sync ## Lint the plugin
	$(BUN) run --cwd "$(PLUGIN_DIR)" lint

.PHONY: plugin-test
plugin-test: plugin-sync ## Run the plugin's unit tests
	$(BUN) run --cwd "$(PLUGIN_DIR)" test

.PHONY: plugin-build
plugin-build: plugin-sync ## Compile the plugin with the Solid transform
	$(BUN) run --cwd "$(PLUGIN_DIR)" build

.PHONY: plugin-package-check
plugin-package-check: plugin-build ## Verify the plugin's npm payload
	$(BUN) run --cwd "$(PLUGIN_DIR)" pack:check

# OpenCode's Solid transform skips node_modules, so the published entry must
# already be compiled; raw JSX ships silently and renders without reactivity.
.PHONY: plugin-entry-check
plugin-entry-check: plugin-build ## Verify the packed plugin entry is precompiled Solid output
	"$(REPO_ROOT)/scripts/check-tui-entry-compiled.sh" "$(PLUGIN_DIR)"

# Deliberately does not depend on plugin-sync: this must resolve the packed
# payload from a clean slate with OpenCode's own Arborist version, because Bun
# only warns on the peer conflicts that make OpenCode's plugin install fail.
.PHONY: plugin-npm-resolve-check
plugin-npm-resolve-check: ## Verify OpenCode's npm installer can resolve the plugin's peers
	"$(REPO_ROOT)/scripts/check-npm-resolution.sh" "$(PLUGIN_DIR)"

# The names from before the plugin moved to the repository root, kept so that
# existing muscle memory and scripts keep working. No `##`, so `make help`
# lists each target once.
TUI_PLUGIN_ALIASES := sync typecheck lint test build package-check entry-check npm-resolve-check

.PHONY: $(addprefix tui-plugin-,$(TUI_PLUGIN_ALIASES))
$(addprefix tui-plugin-,$(TUI_PLUGIN_ALIASES)): tui-plugin-%: plugin-%

endif  # MK_LOCAL_PLUGIN_INCLUDED
