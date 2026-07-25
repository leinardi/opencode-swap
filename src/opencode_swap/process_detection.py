"""Best-effort detection of a running OpenCode process.

OpenCode has no cooperative lock protocol opencode-swap can join (verified:
Auth.set does an unlocked read-merge-write, auth/index.ts:75-79). The
closest thing to a safety net is warning the caller that a swap is racing a
live OpenCode process, which might be mid-refresh and write auth.json again
right after opencode-swap does. Detection failure (no `pgrep`, permission
denied, etc.) degrades to "not detected" rather than raising — this is
advisory, not a security boundary.
"""

from __future__ import annotations

import subprocess

_TIMEOUT = 2.0


def is_opencode_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "opencode"],
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0
