ifndef MK_LOCAL_RUN_INCLUDED
MK_LOCAL_RUN_INCLUDED := 1

.PHONY: run
run: ## Run opencode-swap; pass CLI arguments with ARGS="..."
	$(UV) run opencode-swap $(ARGS)

.PHONY: doctor
doctor: ## Diagnose paths, storage backend, and OpenCode state
	$(UV) run opencode-swap doctor

endif  # MK_LOCAL_RUN_INCLUDED
