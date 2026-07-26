# Security

## Threat model

### In scope

- Accidental disclosure through ordinary filesystem browsing.
- Other non-privileged users on the same machine.
- World/group-readable files or directories caused by bad default
  permissions.
- Tokens appearing in logs, CLI output, or error messages.
- Tokens appearing in process arguments (visible via `ps`/process listings).
- Unsafe temporary files during a write.
- Backups (`.bak`/`.pristine`) containing plaintext credentials at rest.
- Portable account archives exposed during cross-machine transfer.

### Explicitly out of scope

An attacker who already has the user's UID, or access to an unlocked
session, can already read whatever OpenCode itself can read — OpenCode's own
`auth.json` is a plaintext file protected only by `0600` permissions, on
every platform it runs on (see `docs/opencode-auth.md`). `opencode-swap`
protects credentials **at rest at least as well as OpenCode does**, and
better than storing them in a plaintext SQLite database, but it does not —
and cannot — defend against a same-UID adversary, a compromised kernel/root,
or memory scraping. This boundary is deliberate, not an oversight: matching
OpenCode's own trust boundary is the honest target; claiming more would be
false security.

## Storage backend comparison

| Approach | Linux | macOS | Headless Linux | Dependencies | If unavailable |
| --- | --- | --- | --- | --- | --- |
| **OS keychain/keyring** (chosen, primary) | Secret Service via `keyring` | Keychain via `/usr/bin/security` | often absent | `keyring` (Linux only) | falls back to files |
| Encrypted vault (age/libsodium + passphrase) | possible | possible | works | new crypto dependency + passphrase UX | — |
| Keychain-held master key + encrypted blobs | possible | possible | needs fallback | crypto + keyring | same problem, one layer deeper |
| Filesystem-only, `0600` (chosen, fallback) | works everywhere | works everywhere | works | none | n/a — always available |

**Chosen for v1: OS keychain/keyring first, `0600` file fallback, no custom
cryptography.**

- **macOS:** the system `security` CLI, pinned to `/usr/bin/security`
  (not resolved via `PATH`, to prevent an attacker-controlled binary earlier
  on `PATH` from intercepting secrets). The secret is passed via **stdin**,
  not argv, so it never appears in a process listing. This mirrors
  `claude-swap`'s approach exactly, including its reasoning for *not* using
  the third-party `keyring` library on macOS: Keychain items are
  created/read by the same stable system binary, so reads stay silent
  across `opencode-swap` upgrades. A Python-library-based approach
  (`keyring`, or any in-process Security.framework call) ties the item's
  access to the interpreter binary itself, which a tool upgrade can rebuild
  — at which point macOS may show a "wants to use your keychain" prompt.
- **Linux:** the `keyring` Python library, which talks to the Secret
  Service D-Bus API (GNOME Keyring, KWallet, etc.) via `secretstorage`.
  This is genuinely new relative to `claude-swap`, which only supports
  `0600` files on Linux — `opencode-swap` adds the keyring layer because
  plaintext-file-only-by-default is exactly the posture this project set
  out to avoid (see `opencode-balancer`'s plaintext SQLite as the
  motivating counter-example).
- **Fallback (either platform, when the above is unreachable — headless
  servers, containers, no keyring daemon running):** a base64-encoded blob
  in a `0600` file under a `0700` directory. This is **obfuscation, not
  encryption** — anyone with read access to the file can decode it. It's
  the same posture OpenCode's own `auth.json` already has, not a downgrade
  from it.
- **Sticky fallback:** once a keychain/keyring operation fails during a
  process, that `SecretStore` instance pins itself to the file backend for
  the rest of its life — it never flip-flops between backends mid-operation
  even if the backend "recovers" partway through. Account deletion then
  refuses rather than claiming an unreachable OS copy was removed.
  (`store.py`)
- **File-wins-on-read, reconcile-on-write:** if a fallback file exists for
  an account, it's treated as authoritative on read (it may be fresher than
  a stale/unreachable keychain copy from a prior fallback episode); a
  successful keychain/keyring write deletes any stale fallback file for
  that account afterward. Both ported directly from `claude-swap`'s
  `credentials.py`.

Deliberately **not** built for persistent storage: any custom encryption
scheme, app-managed key derivation, or a "vault" file. Those would add real
complexity to the one-command switch workflow without moving the needle
against the actual at-rest threat model — a same-UID attacker can already
read OpenCode's live plaintext `auth.json`.

Portable export is a separate, explicit operation rather than a persistent
vault. It uses standard WinZip AES-256 through `pyzipper`; opencode-swap does
not define cryptographic primitives or its own encrypted envelope. The
passphrase is read from an interactive hidden prompt, never argv, and the
manifest is encrypted and decrypted entirely in memory. Archives are written
atomically with mode `0600`, never overwritten implicitly, and should be
deleted after import. Archive filenames and compressed sizes are not secret;
the sole member always has the generic name `accounts.json`. Users must choose
a strong, unique passphrase; archive security depends on its entropy.

## What's persisted where

| Data | Location | Sensitive? |
| --- | --- | --- |
| Provider-scoped account name, type, account id, email, added timestamp, active hint | `registry.json` (`0600`) | No — never a token |
| Original registry before automatic v1-to-v2 migration | `registry.v1.json.bak` (`0600`) | No — never a token |
| Access token, refresh token, API key | OS keychain/keyring, or `secrets/*.enc` fallback (`0600`) | Yes |
| Pre-switch/pristine/unclaimed `auth.json` snapshots | `backups/*.json` (`0600`, dir `0700`) | Yes — full credential records |
| Explicit portable account export | User-selected path (`0600`, AES-256 encrypted) | Yes — delete after import |
| `opencode-swap`'s own lock file | `.lock` (empty, no data) | No |

The non-secret/secret split is enforced structurally: `AccountMeta` (the
registry dataclass) has no field that could hold a token, and the one place
identity derivation *could* use a credential value (`Provider.identity`) is never
persisted to the registry — only recomputed on demand from data already
loaded from the secret store. See `docs/architecture.md#the-provider-seam`.
Provider implementations enumerate credential fields for archive metadata
filtering. Unstable OAuth identities trigger preservation and refusal instead
of guessing ownership.

## Process hygiene

- No secret is ever passed as a command-line argument (visible via `ps`) —
  the macOS Keychain wrapper passes secrets over stdin specifically to
  avoid this; the Linux `keyring` library's API doesn't take secrets as
  subprocess arguments in the first place.
- CLI output never includes an access token, refresh token, or API key.
  Account ids are shown truncated to their last 4 characters, even though
  an account id alone isn't secret — matching the stated output policy.
  Enforced by tests that plant a known secret value and grep captured
  stdout/stderr for it.
- Every file this project writes uses the same atomic-write primitive
  (`atomic.py`): a `0600` temp file in the target's own directory, then
  `os.replace`. No file is ever created world- or group-readable, even
  momentarily — the mode is set on the temp file *before* the rename that
  makes it visible under its real name.

## The one residual race, and why it's acceptable

OpenCode's own `auth.json` writes are unlocked and non-atomic (see
`docs/opencode-auth.md`) — there is no cooperative lock protocol for an
external tool to join. If `opencode-swap` swaps the file at the exact moment
OpenCode is mid-refresh-write, the two writes can interleave. `opencode-swap`
mitigates this three ways: its own write is atomic (so *its* half of the
race can never leave a half-written file), it detects a likely-running
OpenCode process and prompts for confirmation before switching, and `.bak`/
`restore` provide a recovery path if a race does land badly. It does not
claim to eliminate the race — that would require a change on OpenCode's
side, which is out of this project's control.
