"""Path resolution for OpenCode's auth state and opencode-swap's own data.

Mirrors OpenCode's own path resolution so opencode-swap reads and writes
exactly the file OpenCode does. Verified from OpenCode source
(``packages/core/src/global.ts``, via xdg-basedir) and cross-checked against
opencode-balancer's independent reimplementation (``src/core/path.ts``):

- Data dir: ``$XDG_DATA_HOME/opencode`` if ``XDG_DATA_HOME`` is set (and
  absolute), else ``~/.local/share/opencode``. Identical on Linux and macOS —
  OpenCode uses xdg-basedir defaults everywhere it runs on POSIX; there is no
  macOS-specific data location.
- Auth file: ``<data dir>/auth.json``.
- ``OPENCODE_AUTH_CONTENT``, if set in the *OpenCode process's* environment,
  makes ``Auth.all()`` return that JSON in-memory and ignore the file
  entirely (``auth/index.ts:59-63``). We can only detect whether it is set in
  *our own* process env — useful for ``doctor`` diagnostics, not for writing.
- ``OPENCODE_CONFIG_DIR`` overrides OpenCode's *config* dir only, not the
  data dir — it does not move auth.json (``global.ts:64``).
- ``OPENCODE_TEST_HOME`` overrides OpenCode's notion of home (``global.ts:19``)
  and is honored here too, so integration tests can point both OpenCode and
  opencode-swap at the same fake home without touching the real one.

opencode-swap's own data (registry, backups, fallback secrets) lives under
``$XDG_DATA_HOME/opencode-swap`` using the same resolution — uniform across
Linux and macOS since this is a new tool with no legacy layout to preserve.
"""

from __future__ import annotations

import os
from pathlib import Path

OPENCODE_DIRNAME = "opencode"
OPENCODE_SWAP_DIRNAME = "opencode-swap"
AUTH_FILENAME = "auth.json"


def effective_home() -> Path:
    """Return the home directory, honoring OPENCODE_TEST_HOME for test parity."""
    test_home = os.environ.get("OPENCODE_TEST_HOME")
    if test_home:
        return Path(test_home)
    return Path.home()


def xdg_data_home() -> Path:
    """Return XDG_DATA_HOME, falling back to ``<home>/.local/share``.

    Per the XDG spec (and OpenCode's own xdg-basedir usage), an unset, empty,
    or non-absolute XDG_DATA_HOME is ignored in favor of the default.
    """
    env = os.environ.get("XDG_DATA_HOME", "")
    if env:
        candidate = Path(os.path.expanduser(env))
        if candidate.is_absolute():
            return candidate
    return effective_home() / ".local" / "share"


def get_opencode_data_dir() -> Path:
    """Return OpenCode's data directory (``$XDG_DATA_HOME/opencode``)."""
    return xdg_data_home() / OPENCODE_DIRNAME


def get_opencode_auth_path() -> Path:
    """Return the path to OpenCode's auth.json."""
    return get_opencode_data_dir() / AUTH_FILENAME


def opencode_auth_content_override_active() -> bool:
    """Return True if OPENCODE_AUTH_CONTENT is set in our own process env.

    When set in the *OpenCode* process's environment, OpenCode ignores
    auth.json entirely and reads this instead — a swap would have no effect.
    We can only observe our own env, so this is a best-effort diagnostic
    signal for ``doctor``, not a guarantee about OpenCode's runtime.
    """
    return bool(os.environ.get("OPENCODE_AUTH_CONTENT"))


def get_data_root() -> Path:
    """Return opencode-swap's own data root (``$XDG_DATA_HOME/opencode-swap``)."""
    return xdg_data_home() / OPENCODE_SWAP_DIRNAME
