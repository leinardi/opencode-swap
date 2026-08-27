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

.PHONY: tui-plugin-package-check
tui-plugin-package-check: tui-plugin-sync ## Verify OpenCode TUI plugin npm payload
	$(BUN) run --cwd $(TUI_PLUGIN_DIR) pack:check

# Deliberately does not depend on tui-plugin-sync: this must resolve the packed
# payload with npm from a clean slate, because Bun only warns on the peer
# conflicts that make OpenCode's npm-based plugin install fail outright.
.PHONY: tui-plugin-npm-resolve-check
tui-plugin-npm-resolve-check: ## Verify npm can resolve OpenCode TUI plugin peer dependencies
	"$(REPO_ROOT)/scripts/check-npm-resolution.sh" "$(TUI_PLUGIN_DIR)"

endif  # MK_LOCAL_TUI_PLUGIN_INCLUDED
