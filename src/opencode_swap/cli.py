"""Command-line interface for opencode-swap.

Output rules: never print a secret (access/refresh/api key/token). Account
ids are shown truncated even though they aren't secret themselves, matching
the plan's stated CLI redaction policy.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from opencode_swap import __version__, backup, opencode_auth, paths, process_detection
from opencode_swap.exceptions import AuthFileError, BackupError, OpenCodeSwapError, SchemaError
from opencode_swap.models import ImportConflictAction, JsonObject, Validity
from opencode_swap.providers import get_provider
from opencode_swap.providers.common import is_json_number
from opencode_swap.store import RecordLocation
from opencode_swap.switcher import AccountRefreshResult, RefreshOutcome, Switcher
from opencode_swap.usage import UsageSnapshot, UsageWindow


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-swap",
        description="Multi-account switcher for OpenCode",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    add_p = subparsers.add_parser("add", help="import a provider's active account into secure storage")
    add_p.add_argument("provider", help="OpenCode provider id")
    add_p.add_argument("name", help="name to save the account under")

    list_p = subparsers.add_parser("list", help="list saved accounts")
    list_p.add_argument("provider", nargs="?", help="optional provider id filter")
    list_p.add_argument(
        "--usage",
        action="store_true",
        help="fetch OpenAI/ChatGPT usage where supported (network calls; off by default)",
    )
    current_p = subparsers.add_parser("current", help="show which managed accounts are active")
    current_p.add_argument("provider", nargs="?", help="optional provider id filter")

    status_p = subparsers.add_parser("status", help="show integration status")
    status_p.add_argument("provider", nargs="?", help="optional provider id filter")
    status_p.add_argument("--json", action="store_true", help="emit versioned machine-readable status")
    status_p.add_argument(
        "--usage",
        action="store_true",
        help="fetch usage for active managed accounts where supported (network calls; off by default)",
    )

    use_p = subparsers.add_parser("use", help="switch a provider's active account")
    use_p.add_argument("provider", help="OpenCode provider id")
    use_p.add_argument("name", help="saved account name to activate")
    use_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    switch_p = subparsers.add_parser("switch", help="switch to the next saved account for a provider")
    switch_p.add_argument("provider", help="OpenCode provider id")
    switch_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    refresh_p = subparsers.add_parser("refresh", help="ensure a saved account's OAuth token is valid, refreshing over the network if it's expired")
    refresh_p.add_argument("provider", help="OpenCode provider id")
    refresh_p.add_argument("name", nargs="?", help="saved account name (every saved account for this provider if omitted)")

    remove_p = subparsers.add_parser("remove", help="remove a saved account")
    remove_p.add_argument("provider", help="OpenCode provider id")
    remove_p.add_argument("name", help="saved account name to remove")
    remove_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    rename_p = subparsers.add_parser("rename", help="rename a saved account")
    rename_p.add_argument("provider", help="OpenCode provider id")
    rename_p.add_argument("old", help="current account name")
    rename_p.add_argument("new", help="new account name")

    export_p = subparsers.add_parser("export", help="export saved accounts to a password-encrypted archive")
    export_p.add_argument("path", help="new archive path (must not already exist)")

    import_p = subparsers.add_parser("import", help="import saved accounts from a password-encrypted archive")
    import_p.add_argument("path", help="archive path to import")

    restore_p = subparsers.add_parser("restore", help="restore OpenCode's auth.json from a backup snapshot")
    restore_p.add_argument(
        "--pristine",
        action="store_true",
        help="restore the original pristine snapshot instead of the most recent pre-switch backup",
    )
    restore_p.add_argument(
        "--discard-pending",
        action="store_true",
        help=("archive a retained failed-restore recovery snapshot under backups/ and proceed, instead of refusing to restore"),
    )
    restore_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    subparsers.add_parser("doctor", help="check environment and compatibility")

    return parser


def _redact_account_id(account_id: str | None) -> str:
    if not account_id:
        return "?"
    return f"...{account_id[-4:]}" if len(account_id) > 4 else account_id


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            f"opencode-swap: refusing to prompt on a non-interactive terminal; rerun with --yes to confirm: {prompt}",
            file=sys.stderr,
        )
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _prompt_archive_password(*, confirm: bool) -> str:
    if not sys.stdin.isatty():
        raise OpenCodeSwapError("archive password requires an interactive terminal")
    try:
        password = getpass.getpass("Archive password: ")
        if not password:
            raise OpenCodeSwapError("archive password cannot be empty")
        if confirm and getpass.getpass("Confirm archive password: ") != password:
            raise OpenCodeSwapError("archive passwords do not match")
        return password
    except EOFError as exc:
        raise OpenCodeSwapError("could not read archive password") from exc


def _prompt_archive_extension(path: Path) -> Path:
    """Offer the conventional archive extension only when none was supplied."""
    if path.suffix:
        return path
    while True:
        try:
            answer = input(f"Export path '{path}' has no extension. Add .ocs? [Y/n] ").strip().lower()
        except EOFError as exc:
            raise OpenCodeSwapError("could not read archive extension choice") from exc
        if answer in ("", "y", "yes"):
            return path.with_name(f"{path.name}.ocs")
        if answer in ("n", "no"):
            return path
        print("Enter yes or no.", file=sys.stderr)


def cmd_add(switcher: Switcher, args: argparse.Namespace) -> int:
    meta = switcher.add_account(args.name, provider_id=args.provider)
    print(f"Added '{meta.provider}:{meta.name}' ({meta.email or 'no email'}, account {_redact_account_id(meta.account_id)}).")
    return 0


def cmd_list(switcher: Switcher, args: argparse.Namespace) -> int:
    accounts = switcher.registry.scoped_accounts(args.provider)
    if not accounts:
        print("No accounts saved. Run `opencode-swap add <provider> <name>` after logging into OpenCode.")
        return 0

    active_by_provider = {provider_id: switcher.current(provider_id)[0] for provider_id in {meta.provider for meta in accounts.values()}}
    validity_tag = {Validity.OK: "", Validity.EXPIRED: " (expired)", Validity.INVALID: " (invalid!)"}
    for provider_id, name in sorted(accounts):
        meta = accounts[(provider_id, name)]
        active = active_by_provider[provider_id]
        marker = "*" if active is not None and name == active.name else " "
        # Fetch usage (which may refresh and persist a rotated token) before
        # reading validity, so a successful --usage refresh is reflected in
        # the validity tag on the same line rather than showing a stale
        # "(expired)" next to freshly-fetched numbers.
        usage_suffix = _format_usage(switcher.fetch_usage(name, provider_id=provider_id)) if args.usage else ""
        validity = switcher.account_validity(name, provider_id=provider_id)
        line = f"{marker} {provider_id:<22} {name:<20} {_redact_account_id(meta.account_id):<8} {meta.email or '-':<28}{validity_tag[validity]}{usage_suffix}"
        print(line)
    return 0


def _window_label(window_seconds: object) -> str | None:
    """Derive a short duration label ("5h", "7d", ...) straight from the
    window's own length -- no table of known OpenAI windows, so a changed or
    unfamiliar window length still gets a sensible label (see usage.py's
    key-agnostic window discovery)."""
    if not isinstance(window_seconds, (int, float)) or isinstance(window_seconds, bool):
        return None
    try:
        valid = math.isfinite(window_seconds) and window_seconds > 0
    except OverflowError:
        valid = False
    if not valid:
        return None
    unit_seconds, unit_suffix = next((s, suffix) for s, suffix in ((86_400, "d"), (3600, "h"), (60, "m"), (1, "s")) if window_seconds >= s)
    return f"{round(window_seconds / unit_seconds)}{unit_suffix}"


def _format_reset(reset_at: object) -> str | None:
    if not isinstance(reset_at, (int, float)) or isinstance(reset_at, bool):
        return None
    try:
        if not math.isfinite(reset_at):
            return None
    except OverflowError:
        return None
    try:
        reset_time = datetime.fromtimestamp(reset_at / 1000)
    except (OverflowError, OSError, ValueError):
        return None
    if 0 <= reset_at - time.time() * 1000 < 86_400_000:
        return f"{reset_time:%H:%M}"
    return f"{reset_time:%b} {reset_time.day}, {reset_time:%H:%M}"


def _format_window(window: UsageWindow) -> str | None:
    used_percent = window.used_percent
    try:
        valid_percent = isinstance(used_percent, (int, float)) and not isinstance(used_percent, bool) and math.isfinite(used_percent)
    except OverflowError:
        valid_percent = False
    if not valid_percent:
        return None
    text = f"{used_percent:.0f}%"
    label = _window_label(window.window_seconds)
    if label:
        text = f"{label} {text}"
    reset = _format_reset(window.reset_at)
    if reset:
        text = f"{text} @{reset}"
    return text


def _format_usage(snapshot: UsageSnapshot | None) -> str:
    if snapshot is None:
        return "  usage: n/a"
    if not snapshot.available:
        return f"  usage: unavailable ({snapshot.message})"
    parts = [text for window in snapshot.windows if (text := _format_window(window)) is not None]
    if not parts:
        return "  usage: n/a"
    windows_text = " | ".join(parts)
    if snapshot.plan_name:
        windows_text = f"{windows_text}, {snapshot.plan_name}"
    return "  usage: " + windows_text


def _status_provider_ids(switcher: Switcher, provider_id: str | None, live_auth: dict[str, object]) -> list[str]:
    if provider_id:
        return [provider_id]

    provider_ids = {meta.provider for meta in switcher.registry.scoped_accounts().values()}
    provider_ids.update(live_auth)
    return sorted(provider_ids)


def _status_usage_window(window: UsageWindow) -> dict[str, object] | None:
    try:
        valid_percent = (
            isinstance(window.used_percent, (int, float)) and not isinstance(window.used_percent, bool) and math.isfinite(window.used_percent)
        )
        valid_reset = isinstance(window.reset_at, (int, float)) and not isinstance(window.reset_at, bool) and math.isfinite(window.reset_at)
        valid_window = (
            isinstance(window.window_seconds, (int, float))
            and not isinstance(window.window_seconds, bool)
            and math.isfinite(window.window_seconds)
            and window.window_seconds > 0
        )
    except OverflowError:
        valid_percent = False
        valid_reset = False
        valid_window = False
    if not valid_percent:
        return None
    result: dict[str, object] = {"used_percent": window.used_percent}
    if valid_reset:
        result["reset_at"] = window.reset_at
    if valid_window:
        result["window_seconds"] = window.window_seconds
    return result


def _status_usage(snapshot: UsageSnapshot | None) -> dict[str, object]:
    if snapshot is None:
        return {"applicable": False}

    result: dict[str, object] = {
        "applicable": True,
        "available": snapshot.available,
    }
    if snapshot.available:
        windows = [entry for window in snapshot.windows if (entry := _status_usage_window(window)) is not None]
        if windows:
            result["windows"] = windows
        if snapshot.plan_name:
            result["plan_name"] = snapshot.plan_name
    return result


def _incompatible_reason(provider_id: str, live_auth: dict[str, object], exc: Exception) -> str:
    """`current_from_auth`'s SchemaError can come from two unrelated places:
    OpenCode's own live record for this provider not matching a known shape
    (a real compatibility gap), or opencode-swap's *own* stored secret for a
    managed account failing to parse while resolving which account owns the
    live record (a local secret-store problem with a different remedy: re-run
    `add`, not wait for a schema fix). Re-run just the live-record parse,
    which has no side effects, to tell the two apart for the reported reason.
    """
    try:
        get_provider(provider_id).extract(live_auth)
    except (SchemaError, ValueError):
        return str(exc)
    return f"cannot determine active account: {exc}"


def _status_payload(switcher: Switcher, provider_id: str | None, include_usage: bool) -> dict[str, object]:
    """Build the `status --json` payload.

    Compatibility contract for `schema_version`: it is bumped only for a
    breaking change to this shape (a field removed, renamed, or repurposed).
    Adding a new `active.state` value (as "incompatible" was added here) or a
    new optional field is *not* a bump -- consumers, including the bundled
    TUI plugin (a separately-versioned npm package that can trail the CLI),
    must tolerate unknown `state` values and unknown fields rather than
    assuming the set present at the time they were written is exhaustive.

    `schema_version` went 1 -> 2 when OpenAI added a second (5h) rate-limit
    window: the flat `usage.used_percent`/`reset_at`/`window_seconds` fields
    were replaced by `usage.windows` (a list of the same three fields, one
    entry per window) since a single flat field can no longer represent both
    windows -- an actual removal/repurposing, not an addition.
    """
    accounts = switcher.registry.scoped_accounts(provider_id)
    live_auth = opencode_auth.read_auth(switcher.opencode_auth_path) if switcher.opencode_auth_path.exists() else {}
    providers: list[dict[str, object]] = []
    for current_provider_id in _status_provider_ids(switcher, provider_id, live_auth):
        provider_accounts = [
            {"name": meta.name, "type": meta.type}
            for (stored_provider_id, _), meta in sorted(accounts.items())
            if stored_provider_id == current_provider_id
        ]
        active: dict[str, object]
        try:
            current, desc = switcher.current_from_auth(live_auth, current_provider_id)
        except (SchemaError, ValueError) as exc:
            current = None
            active = {"state": "incompatible", "reason": _incompatible_reason(current_provider_id, live_auth, exc)}
        else:
            if current is not None:
                active = {"state": "managed", "name": current.name}
            elif desc is not None:
                active = {"state": "unmanaged"}
            else:
                active = {"state": "none"}

        entry: dict[str, object] = {
            "id": current_provider_id,
            "accounts": provider_accounts,
            "active": active,
        }
        if include_usage and current is not None:
            entry["usage"] = _status_usage(switcher.fetch_usage(current.name, provider_id=current_provider_id))
        providers.append(entry)
    return {"schema_version": 2, "providers": providers}


def _format_status_usage(usage_snapshot: dict[str, object]) -> str:
    """Render the `usage` block of a `status --json` payload entry as text,
    for `status`'s non-JSON output. Takes the already-built JSON dict (not a
    `UsageSnapshot`) since `cmd_status` only has the payload dict in hand;
    reuses `_window_label`/`_format_reset` so the two commands' text output
    stays in sync."""
    if not usage_snapshot.get("applicable"):
        return "  usage: n/a"
    if not usage_snapshot.get("available"):
        return "  usage: unavailable"
    windows = usage_snapshot.get("windows")
    parts: list[str] = []
    if isinstance(windows, list):
        for window in windows:
            if not isinstance(window, dict):
                continue
            used_percent = window.get("used_percent")
            if not isinstance(used_percent, (int, float)) or isinstance(used_percent, bool):
                continue
            text = f"{used_percent:.0f}%"
            label = _window_label(window.get("window_seconds"))
            if label:
                text = f"{label} {text}"
            reset = _format_reset(window.get("reset_at"))
            if reset:
                text = f"{text} @{reset}"
            parts.append(text)
    if not parts:
        return "  usage: n/a"
    windows_text = " | ".join(parts)
    plan_name = usage_snapshot.get("plan_name")
    if isinstance(plan_name, str) and plan_name:
        windows_text = f"{windows_text}, {plan_name}"
    return "  usage: " + windows_text


def cmd_status(switcher: Switcher, args: argparse.Namespace) -> int:
    payload = _status_payload(switcher, args.provider, args.usage)
    if args.json:
        print(json.dumps(payload, separators=(",", ":"), allow_nan=False))
        return 0

    providers = payload["providers"]
    assert isinstance(providers, list)
    for provider in providers:
        assert isinstance(provider, dict)
        active = provider["active"]
        assert isinstance(active, dict)
        provider_id = provider["id"]
        state = active["state"]
        if state == "managed":
            line = f"{provider_id}: {active['name']}"
        elif state == "unmanaged":
            line = f"{provider_id}: active account opencode-swap doesn't manage"
        elif state == "incompatible":
            line = f"{provider_id}: {active.get('reason')}"
        else:
            line = f"{provider_id}: no active account"
        usage_snapshot = provider.get("usage")
        if isinstance(usage_snapshot, dict):
            line += _format_status_usage(usage_snapshot)
        print(line)
    return 0


def cmd_current(switcher: Switcher, args: argparse.Namespace) -> int:
    provider_ids = [args.provider] if args.provider else sorted({meta.provider for meta in switcher.registry.scoped_accounts().values()})
    if not args.provider and switcher.opencode_auth_path.exists():
        try:
            live_provider_ids = set(opencode_auth.read_auth(switcher.opencode_auth_path))
        except AuthFileError:
            live_provider_ids = set()
        provider_ids = sorted(set(provider_ids) | live_provider_ids)
    if not provider_ids:
        print("No active provider accounts in OpenCode.")
        return 0
    incompatible = False
    for provider_id in provider_ids:
        try:
            meta, desc = switcher.current(provider_id)
        except SchemaError as exc:
            if args.provider:
                raise
            print(f"{provider_id}: unsupported/incompatible ({exc})")
            incompatible = True
            continue
        if desc is None:
            print(f"{provider_id}: no active account")
        elif meta is None:
            print(f"{provider_id}: active account opencode-swap doesn't manage ({_redact_account_id(desc.account_id)})")
        else:
            print(f"{provider_id}: {meta.name} ({meta.email or 'no email'}, account {_redact_account_id(meta.account_id)})")
    return 1 if incompatible else 0


def _can_switch(assume_yes: bool) -> bool:
    prompt = "Switch OpenCode's active account?"
    if process_detection.is_opencode_running():
        prompt = "OpenCode appears to be running; switching now could race an in-flight token refresh. Switch anyway?"
    if not _confirm(prompt, assume_yes):
        print("Aborted.", file=sys.stderr)
        return False
    return True


def cmd_use(switcher: Switcher, args: argparse.Namespace) -> int:
    if not _can_switch(args.yes):
        return 1
    meta = switcher.use_account(args.name, provider_id=args.provider)
    print(f"Switched to '{meta.provider}:{meta.name}'.")
    return 0


def cmd_switch(switcher: Switcher, args: argparse.Namespace) -> int:
    if not _can_switch(args.yes):
        return 1
    next_meta = switcher.next_account(args.provider)
    meta = switcher.use_account(next_meta.name, provider_id=args.provider)
    print(f"Switched to '{meta.provider}:{meta.name}'.")
    return 0


def _refresh_result_tag(result: AccountRefreshResult) -> tuple[str, bool]:
    """(message, is_error). EXPIRED collapses three distinct reasons that
    call for different messages: this account's provider/type has no
    standalone refresh at all (`NO_SUPPORT`, expected and not an error) vs.
    this one genuinely supports refresh but it was deliberately skipped --
    either because OpenCode itself owns this account's refresh (`LIVE`) or
    because live account state couldn't be safely verified this time
    (`AMBIGUOUS`, worth retrying, reported as an error)."""
    if result.validity == Validity.INVALID:
        return "invalid", True
    if result.validity == Validity.OK:
        return "ok", False
    if result.outcome is RefreshOutcome.NO_SUPPORT:
        return "still expired (no standalone refresh available for this account type)", False
    if result.outcome is RefreshOutcome.LIVE:
        return "still expired (OpenCode refreshes this account on its next request)", False
    if result.outcome is RefreshOutcome.AMBIGUOUS:
        return "still expired (live account state could not be confirmed; refresh skipped, try again)", True
    return "still expired", True


def cmd_refresh(switcher: Switcher, args: argparse.Namespace) -> int:
    if args.name:
        names = [args.name]
    else:
        accounts = switcher.registry.scoped_accounts(args.provider)
        names = sorted(name for (provider_id, name) in accounts if provider_id == args.provider)
    if not names:
        print(f"No saved accounts for provider '{args.provider}'.")
        return 0

    exit_code = 0
    for name in names:
        try:
            result = switcher.refresh_account(name, provider_id=args.provider)
        except OpenCodeSwapError as exc:
            print(f"{args.provider:<22} {name:<20} {exc}")
            exit_code = 1
            continue
        tag, is_error = _refresh_result_tag(result)
        if is_error:
            exit_code = 1
        print(f"{args.provider:<22} {name:<20} {tag}")
    return exit_code


def cmd_remove(switcher: Switcher, args: argparse.Namespace) -> int:
    if not _confirm(f"Remove saved account '{args.provider}:{args.name}'?", args.yes):
        print("Aborted.", file=sys.stderr)
        return 1
    switcher.remove_account(args.name, provider_id=args.provider)
    print(f"Removed '{args.provider}:{args.name}'.")
    return 0


def cmd_rename(switcher: Switcher, args: argparse.Namespace) -> int:
    switcher.rename_account(args.old, args.new, provider_id=args.provider)
    print(f"Renamed '{args.provider}:{args.old}' to '{args.provider}:{args.new}'.")
    return 0


def cmd_export(switcher: Switcher, args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    path = _prompt_archive_extension(path)
    count = switcher.export_accounts(path, _prompt_archive_password(confirm=True))
    print(f"Exported {count} account{'s' if count != 1 else ''} to {path}.")
    return 0


def cmd_import(switcher: Switcher, args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    apply_to_all: ImportConflictAction | None = None

    def resolve_conflict(name: str) -> ImportConflictAction:
        nonlocal apply_to_all
        if apply_to_all is not None:
            return apply_to_all
        choices = {
            "s": ImportConflictAction.SKIP,
            "skip": ImportConflictAction.SKIP,
            "sa": ImportConflictAction.SKIP,
            "skip-all": ImportConflictAction.SKIP,
            "o": ImportConflictAction.OVERWRITE,
            "overwrite": ImportConflictAction.OVERWRITE,
            "oa": ImportConflictAction.OVERWRITE,
            "overwrite-all": ImportConflictAction.OVERWRITE,
            "a": ImportConflictAction.ABORT,
            "abort": ImportConflictAction.ABORT,
        }
        while True:
            try:
                answer = (
                    input(f"Account '{name}' already exists. Choose [s/sa/o/oa/a] (skip/skip-all/overwrite/overwrite-all/abort): ").strip().lower()
                )
            except EOFError as exc:
                raise OpenCodeSwapError("could not read import conflict choice") from exc
            action = choices.get(answer)
            if action is not None:
                if answer in ("sa", "skip-all", "oa", "overwrite-all"):
                    apply_to_all = action
                return action
            print("Enter s, sa, o, oa, or a (full names also accepted).", file=sys.stderr)

    count = switcher.import_accounts(path, _prompt_archive_password(confirm=False), resolve_conflict)
    print(f"Imported {count} account{'s' if count != 1 else ''}. Run `opencode-swap use <provider> <name>` to activate one.")
    return 0


def cmd_restore(switcher: Switcher, args: argparse.Namespace) -> int:
    source = "pristine" if args.pristine else "bak"
    which = "original pristine" if args.pristine else "most recent pre-switch"
    prompt = f"Restore OpenCode's auth.json from the {which} backup? This overwrites the current live auth.json."
    if args.discard_pending:
        prompt += " This also archives a retained failed-restore recovery snapshot under backups/ before proceeding."
    if not _confirm(prompt, args.yes):
        print("Aborted.", file=sys.stderr)
        return 1

    metas = switcher.restore(source=source, discard_pending=args.discard_pending)
    if metas:
        accounts = ", ".join(f"{meta.provider}:{meta.name}" for meta in sorted(metas, key=lambda item: (item.provider, item.name)))
        print(f"Restored. Active managed accounts: {accounts}.")
    else:
        print("Restored. No restored provider account could be matched to a managed account.")
    return 0


def _fractional_expiry_warning(auth: JsonObject, provider_id: str) -> str | None:
    """Report an `expires` OpenCode's schema would reject.

    Checked against the raw auth.json value rather than an extracted
    AuthRecord: `extract` reads verbatim and normalization only happens at
    the publication boundary (`providers.common.published_raw`, used by
    `Provider.splice`), so by extraction time the problem is invisible.
    OpenCode types `expires` as `NonNegativeInt` and drops entries that
    fail to decode without any error, so a fractional value looks exactly
    like "this provider was never logged in".
    """
    raw = auth.get(provider_id)
    if not isinstance(raw, dict) or raw.get("type") != "oauth":
        return None
    expires = raw.get("expires")
    if not is_json_number(expires) or float(expires).is_integer():
        return None
    return (
        f"  provider {provider_id!r}: 'expires' is not an integer — OpenCode silently ignores this entry; "
        f"run `opencode-swap use {provider_id} <name>` to repair it"
    )


def cmd_doctor(switcher: Switcher, args: argparse.Namespace) -> int:
    print(f"OpenCode auth file: {switcher.opencode_auth_path}")
    print(f"  exists: {'yes' if switcher.opencode_auth_path.exists() else 'no'}")
    if paths.opencode_auth_content_override_active():
        print("  WARNING: OPENCODE_AUTH_CONTENT is set — OpenCode ignores auth.json entirely while this is set, so switches would have no effect.")

    provider_statuses: list[str] = []
    try:
        if switcher.opencode_auth_path.exists():
            auth = opencode_auth.read_auth(switcher.opencode_auth_path)
            managed_provider_ids = {meta.provider for meta in switcher.registry.scoped_accounts().values()}
            for provider_id in sorted(set(auth) | managed_provider_ids):
                try:
                    get_provider(provider_id).extract(auth)
                except (SchemaError, ValueError) as exc:
                    provider_statuses.append(f"  provider {provider_id!r}: UNSUPPORTED/INCOMPATIBLE: {exc}")
                    continue
                fractional_expiry = _fractional_expiry_warning(auth, provider_id)
                if fractional_expiry is not None:
                    provider_statuses.append(fractional_expiry)
        schema_status = "OK"
    except AuthFileError as exc:
        schema_status = f"UNREADABLE: {exc}"
    except SchemaError as exc:
        schema_status = f"INCOMPATIBLE: {exc}"
    print(f"  schema check: {schema_status}")
    for status in provider_statuses:
        print(status)

    print(f"opencode-swap data dir: {switcher.data_root}")
    accounts = switcher.registry.scoped_accounts()
    locations = [switcher.secrets.record_location(f"{provider}:{name}") for provider, name in accounts]
    sealed_count = locations.count(RecordLocation.SEALED)
    fallback_count = locations.count(RecordLocation.FILE_FALLBACK)
    missing_count = locations.count(RecordLocation.MISSING)
    backend_detail = f"{sealed_count} sealed, {fallback_count} plaintext-fallback, {missing_count} unreadable"
    print(f"  secret backend: {switcher.secrets.backend_name} ({backend_detail})")
    print(f"  managed accounts: {len(accounts)}")
    active = [
        f"{provider}:{name}" for provider in sorted({meta.provider for meta in accounts.values()}) if (name := switcher.registry.get_active(provider))
    ]
    print(f"  active (per registry): {', '.join(active) if active else 'none'}")
    print(f"  .bak present: {'yes' if backup.read_bak(switcher.data_root) is not None else 'no'}")
    print(f"  .pristine present: {'yes' if backup.read_pristine(switcher.data_root) is not None else 'no'}")
    try:
        pending_restore = backup.read_restore_snapshot(switcher.data_root) is not None
        restore_status = "yes" if pending_restore else "no"
    except BackupError:
        restore_status = "unreadable"
    if restore_status != "no":
        print(f"  .restore pending: {restore_status} (run `opencode-swap restore --discard-pending` to clear it)")
    else:
        print(f"  .restore pending: {restore_status}")

    print(f"OpenCode process detected: {'yes' if process_detection.is_opencode_running() else 'no'}")
    return 0


_HANDLERS = {
    "add": cmd_add,
    "list": cmd_list,
    "current": cmd_current,
    "status": cmd_status,
    "use": cmd_use,
    "switch": cmd_switch,
    "refresh": cmd_refresh,
    "remove": cmd_remove,
    "rename": cmd_rename,
    "export": cmd_export,
    "import": cmd_import,
    "restore": cmd_restore,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")

    try:
        switcher = Switcher.default()
        return handler(switcher, args)
    except (OpenCodeSwapError, ValueError) as exc:
        print(f"opencode-swap: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
