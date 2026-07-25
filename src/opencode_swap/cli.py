"""Command-line interface for opencode-swap.

Output rules: never print a secret (access/refresh/api key/token). Account
ids are shown truncated even though they aren't secret themselves, matching
the plan's stated CLI redaction policy.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime

from opencode_swap import __version__, backup, opencode_auth, paths, process_detection
from opencode_swap.exceptions import AuthFileError, OpenCodeSwapError, SchemaError
from opencode_swap.models import Validity
from opencode_swap.providers import PROVIDERS
from opencode_swap.switcher import Switcher
from opencode_swap.usage import UsageSnapshot


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-swap",
        description="Multi-account switcher for OpenCode",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    add_p = subparsers.add_parser("add", help="import the active OpenAI account into secure storage")
    add_p.add_argument("name", help="name to save the account under")

    list_p = subparsers.add_parser("list", help="list saved accounts")
    list_p.add_argument(
        "--usage",
        action="store_true",
        help="fetch and show live OpenAI/ChatGPT usage for each account (network calls; off by default)",
    )
    subparsers.add_parser("current", help="show which managed account is active")

    use_p = subparsers.add_parser("use", help="switch the active OpenAI account")
    use_p.add_argument("name", help="saved account name to activate")
    use_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    remove_p = subparsers.add_parser("remove", help="remove a saved account")
    remove_p.add_argument("name", help="saved account name to remove")
    remove_p.add_argument("-y", "--yes", action="store_true", help="don't prompt for confirmation")

    rename_p = subparsers.add_parser("rename", help="rename a saved account")
    rename_p.add_argument("old", help="current account name")
    rename_p.add_argument("new", help="new account name")

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


def cmd_add(switcher: Switcher, args: argparse.Namespace) -> int:
    meta = switcher.add_account(args.name)
    print(f"Added '{meta.name}' ({meta.email or 'no email'}, account {_redact_account_id(meta.account_id)}).")
    return 0


def cmd_list(switcher: Switcher, args: argparse.Namespace) -> int:
    accounts = switcher.registry.accounts()
    if not accounts:
        print("No accounts saved. Run `opencode-swap add <name>` after logging into OpenCode.")
        return 0

    active = switcher.registry.get_active()
    validity_tag = {Validity.OK: "", Validity.EXPIRED: " (expired)", Validity.INVALID: " (invalid!)"}
    for name in sorted(accounts):
        meta = accounts[name]
        marker = "*" if name == active else " "
        validity = switcher.account_validity(name)
        line = f"{marker} {name:<20} {_redact_account_id(meta.account_id):<8} {meta.email or '-':<28}{validity_tag[validity]}"
        if args.usage:
            line += _format_usage(switcher.fetch_usage(name))
        print(line)
    return 0


def _format_usage(snapshot: UsageSnapshot | None) -> str:
    if snapshot is None:
        return "  usage: n/a"
    if not snapshot.available:
        return f"  usage: unavailable ({snapshot.message})"
    if snapshot.used_percent is None:
        return "  usage: n/a"
    parts = [f"{snapshot.used_percent:.0f}%"]
    if snapshot.reset_at is not None:
        days = math.ceil(max(0, snapshot.reset_at - time.time() * 1000) / 86_400_000)
        reset_time = datetime.fromtimestamp(snapshot.reset_at / 1000)
        parts[0] = f"{days}d {parts[0]} @{reset_time:%b} {reset_time.day}, {reset_time:%H:%M}"
    if snapshot.plan_name:
        parts.append(snapshot.plan_name)
    return "  usage: " + ", ".join(parts)


def cmd_current(switcher: Switcher, args: argparse.Namespace) -> int:
    meta, desc = switcher.current()
    if desc is None:
        print("No active OpenAI account in OpenCode.")
        return 0
    if meta is None:
        print(
            f"OpenCode is logged into an account opencode-swap doesn't manage "
            f"(account {_redact_account_id(desc.account_id)}). Run `opencode-swap add <name>` to manage it."
        )
        return 0
    print(f"{meta.name} ({meta.email or 'no email'}, account {_redact_account_id(meta.account_id)})")
    return 0


def cmd_use(switcher: Switcher, args: argparse.Namespace) -> int:
    if process_detection.is_opencode_running() and not _confirm(
        "OpenCode appears to be running; switching now could race an in-flight token refresh.",
        args.yes,
    ):
        print("Aborted.", file=sys.stderr)
        return 1
    meta = switcher.use_account(args.name)
    print(f"Switched to '{meta.name}'.")
    return 0


def cmd_remove(switcher: Switcher, args: argparse.Namespace) -> int:
    if not _confirm(f"Remove saved account '{args.name}'?", args.yes):
        print("Aborted.", file=sys.stderr)
        return 1
    switcher.remove_account(args.name)
    print(f"Removed '{args.name}'.")
    return 0


def cmd_rename(switcher: Switcher, args: argparse.Namespace) -> int:
    switcher.rename_account(args.old, args.new)
    print(f"Renamed '{args.old}' to '{args.new}'.")
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

    meta = switcher.restore(source=source)
    if meta is not None:
        print(f"Restored. OpenCode is now on managed account '{meta.name}'.")
    else:
        print("Restored. The restored account isn't one opencode-swap manages (or couldn't be identified).")
    return 0


def cmd_doctor(switcher: Switcher, args: argparse.Namespace) -> int:
    print(f"OpenCode auth file: {switcher.opencode_auth_path}")
    print(f"  exists: {'yes' if switcher.opencode_auth_path.exists() else 'no'}")
    if paths.opencode_auth_content_override_active():
        print("  WARNING: OPENCODE_AUTH_CONTENT is set — OpenCode ignores auth.json entirely while this is set, so switches would have no effect.")

    try:
        if switcher.opencode_auth_path.exists():
            auth = opencode_auth.read_auth(switcher.opencode_auth_path)
            PROVIDERS["openai"].extract(auth)
        schema_status = "OK"
    except AuthFileError as exc:
        schema_status = f"UNREADABLE: {exc}"
    except SchemaError as exc:
        schema_status = f"INCOMPATIBLE: {exc}"
    print(f"  schema check: {schema_status}")

    print(f"opencode-swap data dir: {switcher.data_root}")
    print(f"  secret backend: {switcher.secrets.backend_name}")
    accounts = switcher.registry.accounts()
    print(f"  managed accounts: {len(accounts)}")
    print(f"  active (per registry): {switcher.registry.get_active() or 'none'}")
    print(f"  .bak present: {'yes' if backup.read_bak(switcher.data_root) is not None else 'no'}")
    print(f"  .pristine present: {'yes' if backup.read_pristine(switcher.data_root) is not None else 'no'}")

    print(f"OpenCode process detected: {'yes' if process_detection.is_opencode_running() else 'no'}")
    return 0


_HANDLERS = {
    "add": cmd_add,
    "list": cmd_list,
    "current": cmd_current,
    "use": cmd_use,
    "remove": cmd_remove,
    "rename": cmd_rename,
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

    switcher = Switcher.default()
    try:
        return handler(switcher, args)
    except (OpenCodeSwapError, ValueError) as exc:
        print(f"opencode-swap: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
