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

endif  # MK_LOCAL_TUI_PLUGIN_INCLUDED
