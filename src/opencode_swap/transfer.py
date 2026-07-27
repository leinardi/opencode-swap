"""Password-encrypted portable account archive format.

Credential JSON exists only in memory and inside a standard WinZip AES-256
member. pyzipper owns cipher and KDF details; this module only defines the
versioned manifest carried by that archive.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pyzipper  # type: ignore[import-untyped]

from opencode_swap.atomic import atomic_write_bytes_exclusive
from opencode_swap.exceptions import RegistryError, TransferError
from opencode_swap.models import AccountMeta, JsonObject

FORMAT_NAME = "opencode-swap-accounts"
FORMAT_VERSION = 2
MEMBER_NAME = "accounts.json"
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_ACCOUNTS = 1000


@dataclass(frozen=True)
class TransferEntry:
    meta: AccountMeta
    record: JsonObject


def _password_bytes(password: str) -> bytes:
    if not password:
        raise TransferError("archive password cannot be empty")
    return password.encode("utf-8")


def create_archive(entries: list[TransferEntry], password: str) -> bytes:
    if len(entries) > MAX_ACCOUNTS:
        raise TransferError("account export contains too many accounts")
    manifest = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "accounts": [{"name": entry.meta.name, "meta": entry.meta.to_dict(), "record": entry.record} for entry in entries],
    }
    payload = json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise TransferError("account export is too large")

    output = io.BytesIO()
    with pyzipper.AESZipFile(output, "w", compression=pyzipper.ZIP_DEFLATED) as archive:
        archive.setpassword(_password_bytes(password))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        archive.writestr(MEMBER_NAME, payload)
    encrypted = output.getvalue()
    if len(encrypted) > MAX_ARCHIVE_BYTES:
        raise TransferError("account export is too large")
    return encrypted


def write_archive(path: Path, entries: list[TransferEntry], password: str) -> None:
    try:
        atomic_write_bytes_exclusive(path, create_archive(entries, password), mode=0o600)
    except FileExistsError as exc:
        raise TransferError(f"refusing to overwrite existing export file: {path}") from exc
    except OSError as exc:
        raise TransferError(f"could not write export file: {path} ({exc})") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise TransferError("account archive contains duplicate JSON fields")
        result[key] = value
    return result


def _decode_manifest(payload: bytes) -> list[TransferEntry]:
    try:
        data = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError("account archive manifest is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) != {"format", "version", "accounts"}:
        raise TransferError("account archive has an unsupported structure")
    if data["format"] != FORMAT_NAME or type(data["version"]) is not int or data["version"] not in (1, FORMAT_VERSION):
        raise TransferError("account archive has an unsupported format version")
    accounts = data["accounts"]
    if not isinstance(accounts, list) or len(accounts) > MAX_ACCOUNTS:
        raise TransferError("account archive has an invalid account list")

    entries: list[TransferEntry] = []
    names: set[tuple[str, str]] = set()
    for item in accounts:
        if not isinstance(item, dict) or set(item) != {"name", "meta", "record"}:
            raise TransferError("account archive contains an invalid account entry")
        name = item["name"]
        record = item["record"]
        if not isinstance(name, str) or not isinstance(record, dict):
            raise TransferError("account archive contains an invalid account entry")
        try:
            meta = AccountMeta.from_dict(name, item["meta"])
        except RegistryError as exc:
            raise TransferError("account archive contains invalid account metadata") from exc
        account_key = (meta.provider, name)
        if account_key in names:
            raise TransferError("account archive contains duplicate account names")
        if data["version"] == 1 and any(existing_name == name for _provider, existing_name in names):
            raise TransferError("version 1 account archive contains duplicate account names")
        names.add(account_key)
        entries.append(TransferEntry(meta=meta, record=cast(JsonObject, record)))
    return entries


def read_archive(path: Path, password: str) -> list[TransferEntry]:
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise TransferError("account archive is too large")
        encrypted = path.read_bytes()
        if len(encrypted) > MAX_ARCHIVE_BYTES:
            raise TransferError("account archive is too large")
    except FileNotFoundError as exc:
        raise TransferError(f"account archive does not exist: {path}") from exc
    except OSError as exc:
        raise TransferError(f"could not read account archive: {path}") from exc

    try:
        with pyzipper.AESZipFile(io.BytesIO(encrypted), "r") as archive:
            if archive.namelist() != [MEMBER_NAME]:
                raise TransferError("account archive has an unsupported structure")
            info = archive.getinfo(MEMBER_NAME)
            if getattr(info, "wz_aes_strength", None) != 3:
                raise TransferError("account archive is not AES-256 encrypted")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise TransferError("account archive manifest is too large")
            archive.setpassword(_password_bytes(password))
            with archive.open(MEMBER_NAME) as member:
                payload = member.read(MAX_MANIFEST_BYTES + 1)
    except TransferError:
        raise
    except (OSError, RuntimeError, ValueError, pyzipper.BadZipFile) as exc:
        raise TransferError("could not decrypt account archive; password may be incorrect or archive may be corrupt") from exc
    if len(payload) > MAX_MANIFEST_BYTES:
        raise TransferError("account archive manifest is too large")
    return _decode_manifest(payload)
