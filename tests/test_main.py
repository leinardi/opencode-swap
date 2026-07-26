import os
import subprocess
import sys


def test_module_entrypoint_returns_cli_failure_status(tmp_path):
    env = os.environ | {"XDG_DATA_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "opencode_swap", "use", "openai", "ghost", "--yes"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "no such account" in result.stderr
