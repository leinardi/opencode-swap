"""Command-line interface for opencode-swap.

Output rules: never print a secret (access/refresh/api key/token). Account
ids are shown truncated even though they aren't secret themselves, matching
the plan's stated CLI redaction policy.
"""

from __future__ import annotations

import argparse
import getpass
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from opencode_swap import __version__, backup, opencode_auth, paths, process_detection
from opencode_swap.exceptions import AuthFileError, OpenCodeSwapError, SchemaError
from opencode_swap.models import ImportConflictAction, Validity
from opencode_swap.providers import get_provider
from opencode_swap.switcher import Switcher
from opencode_swap.usage import UsageSnapshot


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

    use_p = subparsers.add_parser("use", help="switch a provider's active account")
    use_p.add_argument("provider", help="OpenCode provider id")
    use_p.add_argument("name", help="saved account name to activate")
    use_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    switch_p = subparsers.add_parser("switch", help="switch to the next saved account for a provider")
    switch_p.add_argument("provider", help="OpenCode provider id")
    switch_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

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
        validity = switcher.account_validity(name, provider_id=provider_id)
        line = f"{marker} {provider_id:<22} {name:<20} {_redact_account_id(meta.account_id):<8} {meta.email or '-':<28}{validity_tag[validity]}"
        if args.usage:
            line += _format_usage(switcher.fetch_usage(name, provider_id=provider_id))
        print(line)
    return 0


def _format_usage(snapshot: UsageSnapshot | None) -> str:
    if snapshot is None:
        return "  usage: n/a"
    if not snapshot.available:
        return f"  usage: unavailable ({snapshot.message})"
    try:
        valid_percent = (
            isinstance(snapshot.used_percent, (int, float)) and not isinstance(snapshot.used_percent, bool) and math.isfinite(snapshot.used_percent)
        )
    except OverflowError:
        valid_percent = False
    if not valid_percent:
        return "  usage: n/a"
    parts = [f"{snapshot.used_percent:.0f}%"]
    reset_at = snapshot.reset_at
    if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
        try:
            valid_reset = math.isfinite(reset_at)
        except OverflowError:
            valid_reset = False
        if valid_reset:
            try:
                days = math.ceil(max(0, reset_at - time.time() * 1000) / 86_400_000)
                reset_time = datetime.fromtimestamp(reset_at / 1000)
            except (OverflowError, OSError, ValueError):
                pass
            else:
                parts[0] = f"{days}d {parts[0]} @{reset_time:%b} {reset_time.day}, {reset_time:%H:%M}"
    if snapshot.plan_name:
        parts.append(snapshot.plan_name)
    return "  usage: " + ", ".join(parts)


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
    if not _confirm(
        f"Restore OpenCode's auth.json from the {which} backup? This overwrites the current live auth.json.",
        args.yes,
    ):
        print("Aborted.", file=sys.stderr)
        return 1

    metas = switcher.restore(source=source)
    if metas:
        accounts = ", ".join(f"{meta.provider}:{meta.name}" for meta in sorted(metas, key=lambda item: (item.provider, item.name)))
        print(f"Restored. Active managed accounts: {accounts}.")
    else:
        print("Restored. No restored provider account could be matched to a managed account.")
    return 0


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
        schema_status = "OK"
    except AuthFileError as exc:
        schema_status = f"UNREADABLE: {exc}"
    except SchemaError as exc:
        schema_status = f"INCOMPATIBLE: {exc}"
    print(f"  schema check: {schema_status}")
    for status in provider_statuses:
        print(status)

    print(f"opencode-swap data dir: {switcher.data_root}")
    print(f"  secret backend: {switcher.secrets.backend_name}")
    accounts = switcher.registry.scoped_accounts()
    print(f"  managed accounts: {len(accounts)}")
    active = [
        f"{provider}:{name}" for provider in sorted({meta.provider for meta in accounts.values()}) if (name := switcher.registry.get_active(provider))
    ]
    print(f"  active (per registry): {', '.join(active) if active else 'none'}")
    print(f"  .bak present: {'yes' if backup.read_bak(switcher.data_root) is not None else 'no'}")
    print(f"  .pristine present: {'yes' if backup.read_pristine(switcher.data_root) is not None else 'no'}")

    print(f"OpenCode process detected: {'yes' if process_detection.is_opencode_running() else 'no'}")
    return 0


_HANDLERS = {
    "add": cmd_add,
    "list": cmd_list,
    "current": cmd_current,
    "use": cmd_use,
    "switch": cmd_switch,
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
