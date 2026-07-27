"""AES-256-GCM envelope format for the macOS Keychain backend.

Why this exists: a single OpenAI OAuth record is ~2KB, and hex-encoded for
``security -i`` that's over the 4095-char stdin line limit (see
macos_keychain.py) — so the raw credential can never go into a Keychain
item directly. Instead the Keychain holds a small (32-byte, 64 hex chars)
per-account data key, and the credential itself is AES-256-GCM ciphertext
in the existing atomic 0600 file store. This keeps every operation to one
``security`` spawn regardless of credential size, with no size classes.

pycryptodomex is not a new dependency: pyzipper (already required for
transfer.py's export/import archives) depends on it, and this module
formalizes that as a direct dependency.

Blob layout: b"OCS3" | nonce(12) | tag(16) | ciphertext.
AAD is the caller's key_id (e.g. "openai:work"), so a blob copied or
renamed onto a different account fails to decrypt instead of silently
producing garbage under the wrong identity.
"""

from __future__ import annotations

from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes

from opencode_swap.exceptions import SecretStoreError

_MAGIC = b"OCS3"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16

# Generic message only: never let a decode/decrypt failure path leak
# ciphertext, key material, or plaintext content into an error string.
_CORRUPT_MSG = "stored file credential is corrupt"


def new_data_key() -> str:
    """A fresh 32-byte AES key, hex-encoded (64 chars — printable, tiny)."""
    random_bytes: bytes = get_random_bytes(_KEY_BYTES)
    return random_bytes.hex()


def seal(key_id: str, data_key_hex: str, plaintext: str) -> bytes:
    """Encrypt `plaintext` under `data_key_hex`, bound to `key_id` via AAD."""
    key = _decode_key(data_key_hex)
    nonce: bytes = get_random_bytes(_NONCE_BYTES)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(key_id.encode("utf-8"))
    ciphertext: bytes
    tag: bytes
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return _MAGIC + nonce + tag + ciphertext


def unseal(key_id: str, data_key_hex: str, blob: bytes) -> str:
    """Decrypt a `seal`-produced blob. Raises SecretStoreError on any
    tampering, wrong key, or wrong key_id (AAD mismatch)."""
    key = _decode_key(data_key_hex)
    if len(blob) < len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES or not blob.startswith(_MAGIC):
        raise SecretStoreError(_CORRUPT_MSG)
    rest = blob[len(_MAGIC) :]
    nonce, tag, ciphertext = rest[:_NONCE_BYTES], rest[_NONCE_BYTES : _NONCE_BYTES + _TAG_BYTES], rest[_NONCE_BYTES + _TAG_BYTES :]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(key_id.encode("utf-8"))
    try:
        plaintext: bytes = cipher.decrypt_and_verify(ciphertext, tag)
    except ValueError as exc:
        raise SecretStoreError(_CORRUPT_MSG) from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretStoreError(_CORRUPT_MSG) from exc


def _decode_key(data_key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(data_key_hex)
    except ValueError as exc:
        raise SecretStoreError(_CORRUPT_MSG) from exc
    if len(key) != _KEY_BYTES:
        raise SecretStoreError(_CORRUPT_MSG)
    return key
