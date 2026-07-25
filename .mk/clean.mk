ifndef MK_LOCAL_CLEAN_INCLUDED
MK_LOCAL_CLEAN_INCLUDED := 1

.PHONY: clean
clean: ## Remove Python build artifacts and local tool caches
	rm -rf \
	  "$(REPO_ROOT)/build" \
	  "$(REPO_ROOT)/dist" \
	  "$(REPO_ROOT)/.mypy_cache" \
	  "$(REPO_ROOT)/.pytest_cache" \
	  "$(REPO_ROOT)/.ruff_cache" \
	  "$(REPO_ROOT)"/src/*.egg-info
	find "$(REPO_ROOT)/src" "$(REPO_ROOT)/tests" -type d -name __pycache__ -prune -exec rm -rf {} +

endif  # MK_LOCAL_CLEAN_INCLUDED
