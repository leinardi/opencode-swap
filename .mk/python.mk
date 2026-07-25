ifndef MK_LOCAL_PYTHON_INCLUDED
MK_LOCAL_PYTHON_INCLUDED := 1

.PHONY: python-sync
python-sync: ## Install project and development dependencies
	$(UV) sync --dev

.PHONY: python-test
python-test: ## Run test suite
	$(UV) run pytest -q $(PYTEST_ARGS)

.PHONY: python-build
python-build: ## Build wheel and source distribution
	$(UV) build

endif  # MK_LOCAL_PYTHON_INCLUDED
