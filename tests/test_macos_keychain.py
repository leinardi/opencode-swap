import pytest

from opencode_swap import macos_keychain


def test_oversized_password_never_reaches_security_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(macos_keychain.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(macos_keychain.KeychainError, match="safe stdin limit"):
        macos_keychain.set_password("opencode-swap", "openai:work", "x" * macos_keychain.SECURITY_STDIN_LINE_LIMIT)

    assert calls == []
