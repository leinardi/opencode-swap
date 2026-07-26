ifndef MK_LOCAL_VERIFY_INCLUDED
MK_LOCAL_VERIFY_INCLUDED := 1

.PHONY: verify-python
verify-python: python-test python-build ## Verify tests and package build

.PHONY: verify
verify: check verify-python tui-plugin-typecheck ## Verify pre-commit checks, packages, and TUI plugin types

endif  # MK_LOCAL_VERIFY_INCLUDED
