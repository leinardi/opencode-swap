"""macOS Keychain access via the ``security`` CLI.

Ported from claude-swap's macos_keychain.py. A small wrapper around the
system ``security`` tool for storing generic passwords, used instead of the
third-party ``keyring`` library:

- Keychain items are created and read by the same stable ``security``
  binary, so reads stay silent across upgrades. ``keyring`` (and any
  in-process Security.framework call) anchors the item's access to the
  *Python interpreter*, which ``uv tool upgrade`` rebuilds — at which point
  macOS can show the "wants to use your keychain" prompt. ``security``
  never changes, so creator == reader and there is no prompt.

- ``set_password`` hex-encodes the value (``-X``) and pipes the command
  through ``security -i`` (stdin) so the secret never appears in process
  argv (a process-monitor concern). Falls back to argv only when the
  command would overflow ``security -i``'s 4096-byte stdin line buffer,
  which would otherwise truncate mid-argument and silently corrupt the
  entry.
- ``get_password`` uses ``find-generic-password ... -w`` and treats exit
  code 44 as "not found" (returns ``None``); any *other* non-zero exit
  raises so callers can tell a genuine miss apart from a locked/denied/
  unavailable Keychain.

Caveat: values must be printable text. ``find-generic-password -w`` prints
stored data raw only when it is printable; opencode-swap only ever stores
JSON (ASCII), so this holds — don't reuse this wrapper for binary data.

This module is import-safe on every platform (it only shells out at call
time); its functions are only meaningful on macOS.
"""

from __future__ import annotations

import subprocess

# ``security -i`` reads stdin with a 4096-byte fgets() buffer (BUFSIZ on
# darwin). A command line longer than this is truncated mid-argument: it
# fails to write while leaving any previous entry intact. 64 bytes of
# headroom guards against line-terminator accounting differences.
SECURITY_STDIN_LINE_LIMIT = 4096 - 64

_NOT_FOUND_RC = 44  # errSecItemNotFound surfaced by find/delete-generic-password

# Bound every ``security`` spawn so a wedged Keychain (a locked login
# keychain prompting for an unlock that never comes on a headless/SSH host)
# can't hang the CLI. A healthy Keychain answers in well under 100ms.
_TIMEOUT = 5.0

# Pin the absolute path to Apple's system binary rather than resolving via
# PATH: this is a credential tool, so an attacker-controlled ``security``
# earlier on PATH must not be able to intercept secrets.
_SECURITY = "/usr/bin/security"


class KeychainError(Exception):
    """A ``security`` invocation failed for a reason other than "not found"."""


# The exceptions a Keychain operation may raise that callers should treat as
# "Keychain unusable" (→ fall back to file storage) rather than a
# programming bug. Catching this tuple — never bare Exception — keeps a real
# bug loud instead of silently routing to the file backend mid-invocation.
KEYCHAIN_ERRORS = (KeychainError, subprocess.TimeoutExpired, OSError)


def _quote(value: str) -> str:
    """Quote a value for a ``security -i`` stdin command line."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def get_password(service: str, account: str) -> str | None:
    """Return the stored password, or None if no such item exists (rc 44)."""
    try:
        result = subprocess.run(
            [_SECURITY, "find-generic-password", "-a", account, "-w", "-s", service],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise KeychainError(f"security find-generic-password timed out after {_TIMEOUT}s") from e
    if result.returncode == 0:
        return result.stdout.removesuffix("\n")
    if result.returncode == _NOT_FOUND_RC:
        return None
    raise KeychainError(f"security find-generic-password failed (rc={result.returncode}): {result.stderr.strip()}")


def set_password(service: str, account: str, password: str) -> None:
    """Create or update a generic-password item (``-U``)."""
    hex_value = password.encode("utf-8").hex()
    command = f"add-generic-password -U -a {_quote(account)} -s {_quote(service)} -X {hex_value}\n"
    try:
        if len(command.encode("utf-8")) <= SECURITY_STDIN_LINE_LIMIT:
            result = subprocess.run(
                [_SECURITY, "-i"],
                input=command,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        else:
            result = subprocess.run(
                [_SECURITY, "add-generic-password", "-U", "-a", account, "-s", service, "-X", hex_value],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
    except subprocess.TimeoutExpired as e:
        raise KeychainError(f"security add-generic-password timed out after {_TIMEOUT}s") from e
    if result.returncode != 0:
        raise KeychainError(f"security add-generic-password failed (rc={result.returncode}): {result.stderr.strip()}")


def delete_password(service: str, account: str) -> None:
    """Delete a generic-password item. rc 44 (already absent) counts as success."""
    try:
        result = subprocess.run(
            [_SECURITY, "delete-generic-password", "-a", account, "-s", service],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise KeychainError(f"security delete-generic-password timed out after {_TIMEOUT}s") from e
    if result.returncode in (0, _NOT_FOUND_RC):
        return
    raise KeychainError(f"security delete-generic-password failed (rc={result.returncode}): {result.stderr.strip()}")
