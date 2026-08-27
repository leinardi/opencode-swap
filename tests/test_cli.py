import json
import re
import stat
import time

import pytest

from opencode_swap import backup, cli, process_detection, transfer
from opencode_swap.exceptions import RegistryError
from opencode_swap.models import AccountMeta, Platform
from opencode_swap.oauth_refresh import RefreshedTokens
from opencode_swap.switcher import Switcher
from opencode_swap.usage import UsageSnapshot, UsageWindow
from tests.helpers import make_jwt


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Route paths.py at a throwaway dir and force the file secret backend
    so CLI tests never touch the real OpenCode auth.json or the real OS
    macOS Keychain."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    monkeypatch.setattr(Platform, "detect", classmethod(lambda cls: Platform.UNKNOWN))
    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: False)
    return tmp_path


def auth_path(tmp_path):
    return tmp_path / "opencode" / "auth.json"


def write_live_account(tmp_path, account_id="acct-1", refresh="r1", extra=None):
    entry = {
        "type": "oauth",
        "refresh": refresh,
        "access": make_jwt({"chatgpt_account_id": account_id, "email": f"{account_id}@example.com"}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": account_id,
    }
    data = dict(extra or {})
    data["openai"] = entry
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "opencode-swap" in capsys.readouterr().out


def test_no_args_prints_help_and_exits_zero(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_add_success(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    out = capsys.readouterr().out
    assert "Added 'openai:work'" in out
    assert "acct-1@example.com" in out


def test_add_no_live_account_fails_cleanly(capsys):
    assert cli.main(["add", "openai", "work"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_add_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    combined = "".join(capsys.readouterr())
    assert "super-secret-refresh-token" not in combined


def test_add_does_not_persist_or_print_secret_jwt_email(tmp_path, capsys):
    secret = "super-secret-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh=secret)
    auth = json.loads(auth_path(tmp_path).read_text())
    auth["openai"]["access"] = make_jwt({"chatgpt_account_id": "acct-1", "email": secret})
    auth_path(tmp_path).write_text(json.dumps(auth))

    assert cli.main(["add", "openai", "work"]) == 0

    assert secret not in "".join(capsys.readouterr())
    registry = (tmp_path / "opencode-swap" / "registry.json").read_text()
    assert secret not in registry


def test_list_empty(capsys):
    assert cli.main(["list"]) == 0
    assert "No accounts saved" in capsys.readouterr().out


def test_list_shows_active_marker(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "*" in out
    assert "work" in out


def test_list_does_not_mark_stale_registry_active_account(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="external")
    capsys.readouterr()

    assert cli.main(["list"]) == 0
    assert "*" not in capsys.readouterr().out


def test_list_without_usage_flag_never_touches_network(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("network should not be touched without --usage")

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", boom)
    assert cli.main(["list"]) == 0


def test_list_usage_flag_shows_usage_line(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(
            available=True, plan_name="ChatGPT Plus", windows=(UsageWindow(used_percent=55, reset_at=None, window_seconds=None),)
        ),
    )
    cli.main(["list", "--usage"])
    out = capsys.readouterr().out
    assert "55%" in out
    assert "ChatGPT Plus" in out


def test_list_does_not_tag_healthy_active_account_expired_despite_stale_stored_copy(tmp_path, capsys):
    """Regression: fetch_usage/account_validity must read OpenCode's live
    auth.json for the active account rather than opencode-swap's
    last-captured secret-store snapshot, which goes stale the moment
    OpenCode rotates the token in place."""
    write_live_account(tmp_path, account_id="acct-1", refresh="r1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    data_root = tmp_path / "opencode-swap"
    switcher = Switcher(opencode_auth_path=auth_path(tmp_path), data_root=data_root, platform=Platform.UNKNOWN)
    switcher.secrets.put(
        "openai:work",
        json.dumps(
            {
                "type": "oauth",
                "refresh": "stale-refresh",
                "access": make_jwt({"chatgpt_account_id": "acct-1"}),
                "expires": int((time.time() - 3600) * 1000),
                "accountId": "acct-1",
            }
        ),
    )

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "(expired)" not in out


def test_refresh_no_saved_accounts(capsys):
    assert cli.main(["refresh", "openai"]) == 0
    assert "No saved accounts" in capsys.readouterr().out


def test_refresh_unknown_account_reports_error_without_aborting_other_accounts(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    exit_code = cli.main(["refresh", "openai", "ghost"])
    err_and_out = "".join(capsys.readouterr())
    assert exit_code == 1
    assert "no such account" in err_and_out


def test_refresh_reports_ok_when_token_already_valid(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["refresh", "openai", "work"]) == 0
    out = capsys.readouterr().out
    assert "work" in out
    assert "ok" in out


def test_refresh_all_accounts_for_provider_when_name_omitted(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "a"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "b"])
    capsys.readouterr()

    assert cli.main(["refresh", "openai"]) == 0
    out = capsys.readouterr().out
    assert "a" in out
    assert "b" in out


def test_refresh_never_prints_secrets(tmp_path, capsys, monkeypatch):
    secret = "super-secret-rotated-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh="r1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    data_root = tmp_path / "opencode-swap"
    switcher = Switcher(opencode_auth_path=auth_path(tmp_path), data_root=data_root, platform=Platform.UNKNOWN)
    switcher.secrets.put(
        "openai:work",
        json.dumps(
            {
                "type": "oauth",
                "refresh": "expired-refresh",
                "access": make_jwt({"chatgpt_account_id": "acct-1"}),
                "expires": int((time.time() - 3600) * 1000),
                "accountId": "acct-1",
            }
        ),
    )
    monkeypatch.setattr(
        "opencode_swap.providers.openai.oauth_refresh.refresh_openai_oauth",
        lambda refresh_token, **k: RefreshedTokens(access="new-access", refresh=secret, expires=(time.time() + 3600) * 1000, account_id="acct-1"),
    )

    cli.main(["refresh", "openai", "work"])
    assert secret not in "".join(capsys.readouterr())


def test_refresh_reports_ambiguous_state_distinctly_and_exits_nonzero(tmp_path, capsys, monkeypatch):
    """Regression: an ambiguous live state (here, an in-flight unstable-to-
    stable identity transition for the live-active account) must not be
    reported with the same "no standalone refresh available for this
    account type" message used for providers/types that genuinely have no
    standalone refresh -- OpenAI oauth accounts do support it; this one was
    merely skipped because live ownership couldn't be confirmed."""
    unstable_access = make_jwt({})  # no chatgpt_account_id claim -> unstable identity
    write_live_account(tmp_path, extra=None)
    auth = json.loads(auth_path(tmp_path).read_text())
    auth["openai"] = {"type": "oauth", "refresh": "r1", "access": unstable_access, "expires": int((time.time() + 3600) * 1000)}
    auth_path(tmp_path).write_text(json.dumps(auth))
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    # OpenCode rotates in place: the new live token gains a stable accountId claim.
    stable_access = make_jwt({"chatgpt_account_id": "acct-x"})
    auth_path(tmp_path).write_text(
        json.dumps({"openai": {"type": "oauth", "refresh": "r2-rotated", "access": stable_access, "expires": int((time.time() + 3600) * 1000)}})
    )

    data_root = tmp_path / "opencode-swap"
    switcher = Switcher(opencode_auth_path=auth_path(tmp_path), data_root=data_root, platform=Platform.UNKNOWN)
    switcher.secrets.put(
        "openai:work",
        json.dumps({"type": "oauth", "refresh": "r1", "access": unstable_access, "expires": int((time.time() - 3600) * 1000)}),
    )

    def fail_if_called(*a, **k):
        raise AssertionError("must not standalone-refresh during an identity transition")

    monkeypatch.setattr("opencode_swap.providers.openai.oauth_refresh.refresh_openai_oauth", fail_if_called)

    exit_code = cli.main(["refresh", "openai", "work"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no standalone refresh available for this account type" not in out
    assert "could not be confirmed" in out


def test_usage_columns_align_across_rows_with_different_digit_counts():
    snaps = [
        UsageSnapshot(
            available=True,
            plan_name="Plan A",
            windows=(
                UsageWindow(used_percent=8, reset_at=None, window_seconds=5 * 3600),
                UsageWindow(used_percent=100, reset_at=None, window_seconds=7 * 86_400),
            ),
        ),
        UsageSnapshot(
            available=True,
            plan_name="Plan B",
            windows=(
                UsageWindow(used_percent=100, reset_at=None, window_seconds=5 * 3600),
                UsageWindow(used_percent=3, reset_at=None, window_seconds=7 * 86_400),
            ),
        ),
    ]
    widths = cli._usage_column_widths(snaps)
    lines = [cli._format_usage(snap, widths) for snap in snaps]

    assert lines[0] == "  usage: 5h   8% | 7d 100%, Plan A"
    assert lines[1] == "  usage: 5h 100% | 7d   3%, Plan B"
    # the " | " separator and the plan-name comma land in the same column
    assert len({line.index(" | ") for line in lines}) == 1
    assert len({line.index(", Plan") for line in lines}) == 1


def test_format_usage_without_widths_is_unpadded():
    snap = UsageSnapshot(available=True, windows=(UsageWindow(used_percent=8, reset_at=None, window_seconds=5 * 3600),))
    assert cli._format_usage(snap) == "  usage: 5h 8%"


def test_format_usage_shows_both_windows(monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 1_751_310_000)
    output = cli._format_usage(
        UsageSnapshot(
            available=True,
            windows=(
                UsageWindow(used_percent=49, reset_at=(1_751_310_000 + 3 * 3600) * 1000, window_seconds=5 * 3600),
                UsageWindow(used_percent=5, reset_at=(1_751_310_000 + 7 * 86_400) * 1000, window_seconds=7 * 86_400),
            ),
        )
    )

    assert output.startswith("  usage: 5h 49% @")
    assert " | 7d 5% @" in output
    assert ", " in output.split(" | ")[1]


def test_format_usage_reset_under_a_day_shows_time_only(monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 1_751_310_000)
    output = cli._format_usage(
        UsageSnapshot(
            available=True,
            windows=(UsageWindow(used_percent=49, reset_at=(1_751_310_000 + 3600) * 1000, window_seconds=5 * 3600),),
        )
    )

    # Reset under 24h away renders as bare "HH:MM", not a full date.
    assert "," not in output


@pytest.mark.parametrize(
    ("window_seconds", "expected_label"),
    [(3600, "1h"), (5 * 3600, "5h"), (86_400, "1d"), (7 * 86_400, "7d"), (None, None)],
)
def test_window_label_derives_from_duration(window_seconds, expected_label):
    assert cli._window_label(window_seconds) == expected_label


def test_list_usage_flag_never_prints_secrets(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(
        "opencode_swap.switcher.usage.fetch_openai_oauth_usage",
        lambda *a, **k: UsageSnapshot(available=True, windows=(UsageWindow(used_percent=10, reset_at=None, window_seconds=None),)),
    )
    cli.main(["list", "--usage"])
    assert "super-secret-refresh-token" not in capsys.readouterr().out


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_ZAI_QUOTA_BODY = json.dumps(
    {
        "code": 200,
        "data": {
            "level": "lite",
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247},
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": 12, "nextResetTime": 1788357330998},
            ],
        },
        "success": True,
    }
).encode()


def test_list_usage_flag_renders_zai_windows_from_payload_and_hides_key(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, extra={"zai-coding-plan": {"type": "api", "key": "zai-planted-secret-key"}})
    assert cli.main(["add", "zai-coding-plan", "glm"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", lambda *a, **k: _FakeResponse(_ZAI_QUOTA_BODY))
    assert cli.main(["list", "--usage"]) == 0
    out = capsys.readouterr().out
    assert "5h 8%" in out and "7d 12%" in out
    assert "GLM Coding Lite" in out
    assert "zai-planted-secret-key" not in out


def test_status_json_usage_reports_zai_windows(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, extra={"zai-coding-plan": {"type": "api", "key": "zk"}})
    assert cli.main(["add", "zai-coding-plan", "glm"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", lambda *a, **k: _FakeResponse(_ZAI_QUOTA_BODY))
    assert cli.main(["status", "zai-coding-plan", "--json", "--usage"]) == 0
    usage_block = json.loads(capsys.readouterr().out)["providers"][0]["usage"]
    assert usage_block["available"] is True
    assert usage_block["plan_name"] == "GLM Coding Lite"
    assert [w["window_seconds"] for w in usage_block["windows"]] == [18000, 604800]


def test_list_shows_api_key_last_four_in_the_account_column_not_the_key(tmp_path, capsys):
    write_live_account(tmp_path, extra={"zai-coding-plan": {"type": "api", "key": "zai-full-secret-example-KEY9"}})
    assert cli.main(["add", "zai-coding-plan", "glm"]) == 0
    capsys.readouterr()

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "...KEY9" in out
    assert "zai-full-secret-example" not in out


def test_list_account_column_hint_is_live_even_for_an_account_added_before_the_feature(tmp_path, capsys):
    write_live_account(tmp_path, extra={"zai-coding-plan": {"type": "api", "key": "zai-full-secret-example-KEY9"}})
    assert cli.main(["add", "zai-coding-plan", "glm"]) == 0

    # simulate a pre-feature registry row: account_id never captured
    data_root = tmp_path / "opencode-swap"
    switcher = Switcher(opencode_auth_path=auth_path(tmp_path), data_root=data_root, platform=Platform.UNKNOWN)
    meta = switcher.registry.scoped_accounts()[("zai-coding-plan", "glm")]
    switcher.registry.upsert_account(AccountMeta(meta.name, meta.provider, meta.type, None, meta.email, meta.added))
    capsys.readouterr()

    assert cli.main(["list"]) == 0
    assert "...KEY9" in capsys.readouterr().out


def test_list_columns_stay_aligned_when_a_provider_id_is_longer_than_the_default_width(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", extra={"a-very-long-custom-provider-id": {"type": "api", "key": "short-key"}})
    assert cli.main(["add", "openai", "work"]) == 0
    assert cli.main(["add", "a-very-long-custom-provider-id", "also"]) == 0
    capsys.readouterr()

    assert cli.main(["list"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 2
    # the (fixed-width, 8-char) account-id column starts at the same offset
    # on both rows despite the long custom provider id widening its column
    matches = [re.match(r"^[*\s] (\S+)\s+(\S+)\s+(\S+)\s+", line) for line in lines]
    assert all(matches)
    assert len({m.start(3) for m in matches}) == 1


def test_list_never_prints_secrets(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", refresh="super-secret-refresh-token")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["list"])
    assert "super-secret-refresh-token" not in capsys.readouterr().out


def test_current_none(capsys):
    assert cli.main(["current"]) == 0
    assert "No active" in capsys.readouterr().out


def test_current_unmanaged(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["current"]) == 0
    assert "doesn't manage" in capsys.readouterr().out


def test_current_managed(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()
    cli.main(["current"])
    assert "work" in capsys.readouterr().out


def test_status_json_lists_managed_account_without_secret(tmp_path, capsys):
    secret = "super-secret-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh=secret)
    assert cli.main(["add", "openai", "work"]) == 0
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0
    output = capsys.readouterr().out

    assert json.loads(output) == {
        "schema_version": 2,
        "providers": [
            {
                "id": "openai",
                "accounts": [{"name": "work", "type": "oauth"}],
                "active": {"state": "managed", "name": "work"},
            }
        ],
    }
    assert secret not in output
    assert "acct-1" not in output


def test_status_json_marks_foreign_live_account_unmanaged(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    write_live_account(tmp_path, account_id="foreign")
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["providers"][0]["active"] == {"state": "unmanaged"}


def test_status_malformed_auth_fails_loud_without_saved_accounts(tmp_path, capsys):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")

    assert cli.main(["status", "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "opencode-swap:" in captured.err


def test_status_malformed_auth_fails_loud_with_saved_accounts(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    auth_path(tmp_path).write_text("{")
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "opencode-swap:" in captured.err


def test_status_usage_fetches_only_active_managed_account(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    write_live_account(tmp_path, account_id="acct-2")
    assert cli.main(["add", "openai", "personal"]) == 0
    assert cli.main(["use", "openai", "work", "--yes"]) == 0
    capsys.readouterr()
    calls = []

    def fetch(*args):
        calls.append(args)
        return UsageSnapshot(
            available=True,
            plan_name="ChatGPT Plus",
            windows=(
                UsageWindow(used_percent=49, reset_at=1_749_900_000_000, window_seconds=18_000),
                UsageWindow(used_percent=17, reset_at=1_750_000_000_000, window_seconds=604_800),
            ),
        )

    monkeypatch.setattr("opencode_swap.switcher.usage.fetch_openai_oauth_usage", fetch)
    assert cli.main(["status", "--json", "--usage"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert len(calls) == 1
    assert result["providers"][0]["usage"] == {
        "applicable": True,
        "available": True,
        "plan_name": "ChatGPT Plus",
        "windows": [
            {"used_percent": 49, "reset_at": 1_749_900_000_000, "window_seconds": 18_000},
            {"used_percent": 17, "reset_at": 1_750_000_000_000, "window_seconds": 604_800},
        ],
    }


def test_status_without_usage_never_touches_network(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    capsys.readouterr()

    def boom(*args, **kwargs):
        raise AssertionError("network should not be touched without --usage")

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", boom)
    assert cli.main(["status", "--json"]) == 0


def test_status_usage_does_not_query_unmanaged_active_account(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    write_live_account(tmp_path, account_id="foreign")
    capsys.readouterr()

    def boom(*args, **kwargs):
        raise AssertionError("usage must not query a foreign active account")

    monkeypatch.setattr("opencode_swap.usage.urllib.request.urlopen", boom)
    assert cli.main(["status", "--json", "--usage"]) == 0
    assert "usage" not in json.loads(capsys.readouterr().out)["providers"][0]


def test_status_reports_incompatible_provider_without_failing_the_whole_command(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1", extra={"anthropic": {"type": "oauth", "refresh": "r", "access": "a", "expires": 0}})
    assert cli.main(["add", "openai", "work"]) == 0
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    by_id = {provider["id"]: provider for provider in result["providers"]}
    assert by_id["openai"]["active"] == {"state": "managed", "name": "work"}
    assert by_id["anthropic"]["active"]["state"] == "incompatible"
    assert "anthropic" in by_id["anthropic"]["active"]["reason"]

    assert cli.main(["status"]) == 0
    text_output = capsys.readouterr().out
    assert "openai: work" in text_output
    assert "anthropic: anthropic auth type is not supported by opencode-swap" in text_output


def test_status_distinguishes_corrupt_local_secret_from_unsupported_provider(tmp_path, capsys):
    """A managed account's own stored secret failing to parse is a local
    secret-store problem (re-`add` fixes it), not an OpenCode-schema
    incompatibility -- the reported reason must not conflate the two."""
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    switcher = cli.Switcher.default()
    switcher.secrets.put("openai:work", "{")  # corrupt the stored secret
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    active = result["providers"][0]["active"]
    assert active["state"] == "incompatible"
    assert "not valid JSON" in active["reason"]
    assert "not supported by opencode-swap" not in active["reason"]


def test_current_explicit_incompatible_provider_fails(tmp_path, capsys):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openai": {"type": "oauth"}}))

    assert cli.main(["current", "openai"]) == 1
    assert "missing/invalid" in capsys.readouterr().err


def test_switcher_initialization_failure_is_reported_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(cli.Switcher, "default", lambda: (_ for _ in ()).throw(RegistryError("migration failed")))

    assert cli.main(["list"]) == 1
    captured = capsys.readouterr()
    assert "opencode-swap: migration failed" in captured.err
    assert "Traceback" not in captured.err


def test_use_switches_account(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    capsys.readouterr()

    assert cli.main(["use", "openai", "work", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"


def test_switch_cycles_accounts_and_wraps(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    capsys.readouterr()

    assert cli.main(["switch", "openai", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"

    assert cli.main(["switch", "openai", "--yes"]) == 0
    assert "Switched to 'openai:personal'" in capsys.readouterr().out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-2"


def test_switch_without_managed_active_account_fails_cleanly(capsys):
    assert cli.main(["switch", "openai"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_use_unknown_account_fails_cleanly(capsys):
    assert cli.main(["use", "openai", "ghost", "--yes"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_use_prompts_when_opencode_running_and_aborts_non_tty(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "openai", "work"]) == 1  # non-tty, no --yes -> refuses
    assert "Aborted" in capsys.readouterr().err


def test_use_requires_confirmation_when_opencode_is_not_running(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["use", "openai", "work"]) == 1
    assert "Aborted" in capsys.readouterr().err


def test_use_yes_flag_skips_confirmation_even_when_running(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: True)
    assert cli.main(["use", "openai", "work", "--yes"]) == 0
    assert "Switched to 'openai:work'" in capsys.readouterr().out


def test_remove_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "openai", "work"]) == 1
    assert "Aborted" in capsys.readouterr().err
    assert "work" in cli.Switcher.default().registry.accounts()


def test_remove_yes_flag(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["remove", "openai", "work", "--yes"]) == 0
    assert "Removed 'openai:work'" in capsys.readouterr().out
    assert cli.Switcher.default().registry.accounts() == {}


def test_rename(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["rename", "openai", "work", "personal"]) == 0
    assert "Renamed 'openai:work' to 'openai:personal'" in capsys.readouterr().out
    assert "personal" in cli.Switcher.default().registry.accounts()


def test_rename_unknown_fails_cleanly(capsys):
    assert cli.main(["rename", "openai", "ghost", "new"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_export_import_roundtrip_with_hidden_password(tmp_path, monkeypatch, capsys):
    secret = "super-secret-refresh-token"
    write_live_account(tmp_path, account_id="acct-1", refresh=secret)
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts.ocs"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    passwords = iter(["password", "password", "password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(passwords))

    assert cli.main(["export", str(archive)]) == 0
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "mac"))
    assert cli.main(["import", str(archive)]) == 0

    combined = "".join(capsys.readouterr())
    assert secret not in combined
    assert secret.encode() not in archive.read_bytes()
    assert "work" in cli.Switcher.default().registry.accounts()


def test_export_offers_ocs_extension_for_extensionless_path(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    capsys.readouterr()

    assert cli.main(["export", str(archive)]) == 0

    assert archive.with_suffix(".ocs").exists()
    assert not archive.exists()
    assert str(archive.with_suffix(".ocs")) in capsys.readouterr().out


def test_export_keeps_extensionless_path_when_ocs_extension_declined(tmp_path, monkeypatch, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    assert cli.main(["add", "openai", "work"]) == 0
    archive = tmp_path / "accounts"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    capsys.readouterr()

    assert cli.main(["export", str(archive)]) == 0

    assert archive.exists()
    assert not archive.with_suffix(".ocs").exists()


@pytest.mark.parametrize(
    ("choice", "expected_count", "expected_ids"),
    [
        ("sa", 0, {"a": "old-a", "b": "old-b"}),
        ("oa", 2, {"a": "new-a", "b": "new-b"}),
    ],
)
def test_import_conflict_apply_to_all(  # noqa: PLR0913, PLR0917
    choice, expected_count, expected_ids, tmp_path, monkeypatch, capsys
):
    for name in ("a", "b"):
        write_live_account(tmp_path, account_id=f"old-{name}")
        assert cli.main(["add", "openai", name]) == 0
    entries = [
        transfer.TransferEntry(
            AccountMeta(name, "openai", "oauth", f"new-{name}", None, "2026-01-01T00:00:00Z"),
            {
                "type": "oauth",
                "refresh": f"refresh-{name}",
                "access": make_jwt({"chatgpt_account_id": f"new-{name}"}),
                "expires": int((time.time() + 3600) * 1000),
                "accountId": f"new-{name}",
            },
        )
        for name in ("a", "b")
    ]
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, entries, "password")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")
    prompts = []

    def choose(prompt):
        prompts.append(prompt)
        return choice

    monkeypatch.setattr("builtins.input", choose)
    capsys.readouterr()

    assert cli.main(["import", str(archive)]) == 0

    assert len(prompts) == 1
    assert "[s/sa/o/oa/a]" in prompts[0]
    assert f"Imported {expected_count} account" in capsys.readouterr().out
    accounts = cli.Switcher.default().registry.accounts()
    assert {name: accounts[name].account_id for name in ("a", "b")} == expected_ids


def test_export_refuses_noninteractive_password_prompt(tmp_path, capsys):
    write_live_account(tmp_path)
    cli.main(["add", "openai", "work"])
    capsys.readouterr()

    assert cli.main(["export", str(tmp_path / "accounts.ocs")]) == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_import_list_never_prints_another_account_credential(tmp_path, monkeypatch, capsys):
    secret = "account-a-super-secret-refresh"
    record_a = {
        "type": "oauth",
        "refresh": secret,
        "access": make_jwt({"chatgpt_account_id": "acct-a"}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": "acct-a",
    }
    record_b = {
        "type": "oauth",
        "refresh": "refresh-b",
        "access": make_jwt({"chatgpt_account_id": "acct-b", "email": secret}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": "acct-b",
    }
    entries = [
        transfer.TransferEntry(AccountMeta("a", "openai", "oauth", "acct-a", None, "2026-01-01T00:00:00Z"), record_a),
        transfer.TransferEntry(AccountMeta("b", "openai", "oauth", "acct-b", None, "2026-01-01T00:00:00Z"), record_b),
    ]
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, entries, "password")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "password")

    assert cli.main(["import", str(archive)]) == 0
    assert cli.main(["list"]) == 0

    assert secret not in "".join(capsys.readouterr())
    assert secret not in (tmp_path / "opencode-swap" / "registry.json").read_text()


def test_doctor_runs_clean_with_no_state(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OpenCode auth file:" in out
    assert "secret backend:" in out
    assert ".bak present: no" in out
    assert ".pristine present: no" in out
    assert ".restore pending: no" in out


def test_doctor_reports_backups_present_after_a_switch(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    capsys.readouterr()

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert ".bak present: yes" in out
    assert ".pristine present: yes" in out


def test_restore_requires_confirmation_non_tty(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    capsys.readouterr()

    assert cli.main(["restore"]) == 1
    assert "Aborted" in capsys.readouterr().err


def test_restore_bak_with_yes(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    cli.main(["use", "openai", "personal", "--yes"])
    capsys.readouterr()

    assert cli.main(["restore", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Restored" in out
    assert "work" in out
    assert json.loads(auth_path(tmp_path).read_text())["openai"]["accountId"] == "acct-1"


def test_restore_no_backup_fails_cleanly(capsys):
    assert cli.main(["restore", "--yes"]) == 1
    assert "opencode-swap:" in capsys.readouterr().err


def test_doctor_reports_pending_restore_snapshot(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    switcher = cli.Switcher.default()
    backup.write_restore_snapshot(switcher.data_root, {"openai": {"type": "oauth"}})
    capsys.readouterr()

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert ".restore pending: yes" in out
    assert "--discard-pending" in out


def test_restore_discard_pending_clears_a_stuck_snapshot(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    write_live_account(tmp_path, account_id="acct-2")
    cli.main(["add", "openai", "personal"])
    cli.main(["use", "openai", "work", "--yes"])
    cli.main(["use", "openai", "personal", "--yes"])
    switcher = cli.Switcher.default()
    # Simulate a crash between write_restore_snapshot and the live replace:
    # a pending snapshot whose content no longer matches live auth.json.
    backup.write_restore_snapshot(switcher.data_root, {"openai": {"type": "oauth"}})
    capsys.readouterr()

    # Without --discard-pending, the stuck snapshot still blocks restore.
    assert cli.main(["restore", "--yes"]) == 1
    assert "--discard-pending" in capsys.readouterr().err

    assert cli.main(["restore", "--yes", "--discard-pending"]) == 0
    out = capsys.readouterr().out
    assert "Restored" in out
    assert not (switcher.data_root / "backups" / backup.RESTORE_SNAPSHOT_FILENAME).exists()


def test_doctor_reports_auth_content_override(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "{}")
    cli.main(["doctor"])
    assert "OPENCODE_AUTH_CONTENT" in capsys.readouterr().out


def test_doctor_reports_incompatible_schema(tmp_path, capsys):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openai": {"type": "oauth"}}))  # missing required fields
    cli.main(["doctor"])
    assert "INCOMPATIBLE" in capsys.readouterr().out


def test_doctor_reports_fractional_expiry(tmp_path, capsys):
    """A float `expires` parses fine here but makes OpenCode drop the entry
    silently, so `doctor` must call it out rather than reporting OK."""
    write_live_account(tmp_path, account_id="acct-1")
    path = auth_path(tmp_path)
    data = json.loads(path.read_text())
    data["openai"]["expires"] = float(data["openai"]["expires"]) + 0.0754
    path.write_text(json.dumps(data))

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "schema check: OK" in out  # opencode-swap itself still reads it
    assert "'expires' is not an integer" in out
    assert "opencode-swap use openai" in out


def test_doctor_stays_quiet_for_an_integer_expiry(tmp_path, capsys):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["doctor"])
    assert "'expires' is not an integer" not in capsys.readouterr().out


def test_data_dir_created_with_safe_permissions(tmp_path):
    write_live_account(tmp_path, account_id="acct-1")
    cli.main(["add", "openai", "work"])
    data_root = tmp_path / "opencode-swap"
    assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_root / "registry.json").stat().st_mode) == 0o600
