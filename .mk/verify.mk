ifndef MK_LOCAL_VERIFY_INCLUDED
MK_LOCAL_VERIFY_INCLUDED := 1

.PHONY: verify-python
verify-python: python-test python-build ## Verify tests and package build

.PHONY: verify
verify: check verify-python tui-plugin-typecheck tui-plugin-lint tui-plugin-package-check tui-plugin-npm-resolve-check ## Verify pre-commit checks and packages

endif  # MK_LOCAL_VERIFY_INCLUDED
