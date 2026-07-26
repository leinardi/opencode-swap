# Architecture

## Tech stack

- **Language/runtime:** Python 3.12+.
- **CLI framework:** stdlib `argparse` (no third-party CLI framework — the
  command surface is small enough that a framework would be net overhead).
- **Packaging:** `hatchling` + `uv` (`pyproject.toml`), console scripts
  `opencode-swap` and `ocs`.
- **Dependencies:** `keyring` on Linux for Secret Service, plus `pyzipper`
  for password-encrypted portable account archives. macOS goes through a
  subprocess wrapper around the system `/usr/bin/security` CLI instead of a
  Python keychain dependency.
- **Testing:** `pytest`, no other test dependencies. No network access, no
  real OS keychain/keyring access, no real `auth.json` access required to
  run the suite (see `docs/testing.md`).

This mirrors `claude-swap`'s stack choice deliberately (same language,
build system, and package-manager conventions) so the two tools stay easy
to maintain side by side, and because `claude-swap`'s design — atomic
writes, transaction/rollback, keychain-with-fallback — was the reference
this project was modeled on.

## Module map

```
src/opencode_swap/
  cli.py              argparse wiring; formats output; never touches secrets directly
  switcher.py         orchestration: add/use/remove/rename/restore/current, the FileLock scope
  store.py            SecretStore (keychain/keyring/file routing) + Registry (registry.json)
  models.py           dataclasses & enums: AccountMeta, AuthRecord, AccountDesc, Validity,
                       SwitchTransaction, Platform, normalize_account_name
  opencode_auth.py     read/atomic-write of OpenCode's auth.json (whole-file, generic)
  providers/base.py    the Provider protocol (the one seam that varies per provider)
  providers/openai.py  the only Provider implementation in v1
  oauth_jwt.py         JWT claim decoding (accountId/email extraction) — no network
  paths.py             resolves OpenCode's auth.json path and opencode-swap's own data dir
  backup.py            .bak / .pristine / unclaimed-*.json snapshots
  transfer.py          versioned password-encrypted account archive format
  locking.py           FileLock (fcntl-based, POSIX only)
  macos_keychain.py    subprocess wrapper around /usr/bin/security
  process_detection.py best-effort "is OpenCode running" check
  exceptions.py        exception hierarchy
  atomic.py            shared atomic-write primitive (temp file + chmod + rename)
```

Responsibility boundaries are deliberate:

- `opencode_auth.py` knows nothing about *which* provider is stored where —
  it treats `auth.json` as an opaque JSON object and only guarantees safe
  I/O.
- `providers/openai.py` knows nothing about *how* auth.json is read/written,
  or where secrets are stored — it only interprets one key's record.
- `store.py` knows nothing about accounts or providers — it's a generic
  string key-value secret store plus a generic name→metadata registry.
- `switcher.py` is the only module that ties all of the above together into
  actual operations, and the only place that holds the lock.

This means adding a second provider is: write a new `providers/xyz.py`
implementing the `Provider` protocol, register it in `providers/__init__.py`,
and nothing else changes. `switcher.py` already dispatches by
`meta.provider` / a `provider_id` parameter throughout.

## The Provider seam

```python
class Provider(Protocol):
    id: str
    def keys(self) -> list[str]
    def extract(self, auth: dict) -> AuthRecord | None
    def splice(self, auth: dict, record: AuthRecord) -> dict
    def identity(self, record: AuthRecord) -> str
    def describe(self, record: AuthRecord) -> AccountDesc
    def validate(self, record: AuthRecord) -> Validity
```

This is deliberately the *smallest* interface that made the real OpenAI
provider implementation possible — it was derived from what
`providers/openai.py` actually needed, not designed speculatively for
providers that don't exist yet. In particular there's no
`DiscoverActiveCredentials`/`ActivateAccount` ceremony: the whole-file
read/write is generic (`opencode_auth.py`), so a provider only needs to
know how to read and write *its own* corner of the file.

`identity()` deserves a note: for an OAuth record it prefers the OpenAI
`accountId` claim, falling back to the raw refresh token string if no
account id is available yet. This mirrors `opencode-balancer`'s
`authIdentityKey` approach (a second, independent implementation that
arrived at the same fallback chain from reading the same OpenCode source),
and is why `AccountMeta` (the *non-secret* registry) deliberately never
caches the identity string — the refresh-token fallback would otherwise leak
a secret into `registry.json`.

## Storage layout

```
$XDG_DATA_HOME/opencode-swap/            (0700)
  registry.json                          (0600) — name -> {provider, type, accountId, email, added}
  backups/                               (0700)
    auth.json.bak                        (0600) — most recent pre-switch snapshot
    auth.json.pristine                   (0600) — first-ever snapshot, written once
    auth.json.restore                    (0600) — temporary restore source; retained if restore fails
    unclaimed-<provider>-<ts>-<suffix>.json (0600) — foreign login preserved instead of overwritten
  secrets/                               (0700, only created if the file fallback is used)
    v2-<sha256-key>.enc                  (0600) — base64-obfuscated record, fallback only;
                                          legacy openai_<name>.enc files migrate on write
  .lock                                  — FileLock target

OpenCode's own state (read + atomically rewritten):
  $XDG_DATA_HOME/opencode/auth.json      (0600, owned by OpenCode)
```

`registry.json` never contains a token. The actual OAuth/API-key record for
each saved account lives in the OS keychain (macOS) / keyring (Linux) under
service `opencode-swap`, keyed by `"<provider>:<name>"`, or in the `secrets/`
fallback files when no keychain/keyring is reachable.

## The switch algorithm (`Switcher.use_account`)

```
use <target>:
  acquire FileLock(data_root/.lock)                    # serialize our own invocations
  target = registry.accounts()[name]                   # error if unknown
  target_record = secrets.get("<provider>:<name>")      # error if missing (out-of-sync store)

  live = read auth.json (or {} if it doesn't exist yet)
  write_pristine_if_absent(live)                        # first-ever snapshot, once

  # --- sync-back ---
  live_record = provider.extract(live)
  if live_record belongs to a *managed* account (identity match):
      if it differs from what's stored for that account:
          secrets.put(owner, live_record)                # capture rotation
  elif live_record exists but matches no managed account:
      backup.write_unclaimed(live_record)                 # foreign login, don't lose it
  # -----------------

  backup.write_bak(live)                                 # pre-swap snapshot
  new_auth = provider.splice(live, target_record)         # merge into a fresh copy
  atomic_write_auth(new_auth)                             # temp(0600) + os.replace
  registry.set_active(name)

  release lock
```

Everything before the `atomic_write_auth` call is either a read, or a write
to opencode-swap's *own* storage (secret store, `.bak`) — none of it touches
OpenCode's live state. The atomic write is the one moment OpenCode's world
changes, and it's genuinely atomic (temp file in the same directory, then
`os.replace`), so a crash during that specific call can never leave a
truncated or partial `auth.json` — it either fully lands or the original
file is untouched.

### Why sync-back is mandatory

OpenCode rotates the OAuth access *and* refresh token in place on every
token refresh, rewriting `auth.json` itself
(see `docs/opencode-auth.md#refresh`). If `opencode-swap` didn't recapture
that rotation before switching away from an account, the *next* time that
account is reactivated it would carry a dead refresh token — and since the
rotated token only ever existed in `auth.json`, which is about to be
overwritten, it would be gone for good, not just stale. This is why
sync-back happens unconditionally on every `use`, not just when the caller
asks for it.

### Transaction and rollback

`SwitchTransaction` records which steps completed
(`sync_captured` / `unclaimed_stashed` / `bak_written` / `auth_written` /
`registry_written`). If anything raises, `_rollback` runs — but it only has
one real job: **if `auth_written` happened, restore `auth.json` to its
pre-switch content.** Every step before that either hasn't touched OpenCode's
live state yet (so there's nothing to undo), or — for the atomic write
itself — is guaranteed by construction to leave the original file untouched
on failure. The one case rollback actually matters for in practice: the
`atomic_write_auth` call succeeds, but the subsequent `registry.set_active`
call fails (disk error, corrupt registry, etc.) — without rollback, OpenCode
would be left running the new account while opencode-swap's own bookkeeping
still thought the old one was active.

Note `registry.json`'s `active` field is informational only — `current()`
always re-derives "who's actually active" by matching the live record's
identity against saved accounts, not by trusting the `active` field. This is
deliberate: even if `set_active` silently drifted, `current` still reports
the truth.

### Backups and recovery

`.bak` and `.pristine` exist so a human (or `opencode-swap restore`) can
recover even if the transaction rollback itself can't run (e.g.
opencode-swap was killed outright). `restore` chains the *current* live
state into `.bak` before overwriting it, so a restore is itself always
undoable by restoring again — and it tolerates the live file being
corrupted JSON (that's exactly the scenario it exists for) as well as a
backed-up record that can no longer be interpreted (the restore still
succeeds; only "which account is this" identification is skipped).

## Portable account transfer

`export <path>` packages every registry entry with its corresponding secret
record in a versioned manifest, then encrypts that manifest in memory using
the standard WinZip AES-256 format implemented by `pyzipper`. The encrypted
archive is published atomically with mode `0600`; plaintext credentials are
never written to a temporary file. Export holds `Switcher.lock` and first
captures recognizable live token rotation, for the same reason sync-back is
mandatory before a switch. If the live record does not match a managed
identity but has the same provider and type as the registry-active account,
export refuses: an account-id/refresh-token identity transition cannot safely
be distinguished from a foreign login.

`import <path>` decrypts in memory and strictly validates the archive version,
manifest shape, provider schema, account names, record types, and duplicate
identities. While holding `Switcher.lock`, it then preflights every destination
name, identity, and unregistered secret key. Any conflict aborts before the
first write. New secrets are written through `SecretStore`, followed by one
atomic registry publication; a failure attempts to remove every newly written
secret. Printable metadata is derived from validated records and filtered
against credential fields from every account in the archive, preventing one
account's token from entering another account's registry metadata. Import never
changes OpenCode's live `auth.json` or the destination registry's active marker.

## Concurrency

Two levels:

- **opencode-swap vs. itself:** `FileLock` (`fcntl.flock`, exclusive,
  non-blocking, polled with a timeout) around `data_root/.lock`, held across
  every mutating `Switcher` method. Verified under real multi-thread
  contention (`tests/test_locking.py`,
  `tests/test_concurrency.py`) — never more than one holder at a time, and a
  race between two switches always ends with a valid, complete `auth.json`
  matching exactly one account, never a corrupted or blended file.
- **opencode-swap vs. a running OpenCode:** there is no cooperative lock to
  join — OpenCode's own `Auth.set` does an unlocked, non-atomic
  read-merge-write (see `docs/opencode-auth.md`). `process_detection.py`
  does a best-effort check (`pgrep -x opencode`) and the CLI warns/prompts
  before switching if it's running. This is advisory, not a real mutex —
  the honest fail-safe here is opencode-swap's own atomic write (so *its*
  side can never be caught half-written), plus `.bak`/`restore` for
  recovering if a race did land badly.

## Compatibility posture

OpenCode's `auth.json` path, JSON shape, and provider key names are
undocumented internals that can change across versions. `providers/openai.py`
validates every record it reads against the known shape and raises
`SchemaError` — never silently drops or guesses at fields — so an
incompatible future OpenCode version fails loud (`doctor`/`current`/`add`/
`use` all surface it) instead of corrupting state. See
`docs/opencode-auth.md` for exactly which behaviors are cited from source
vs. inferred, and `AGENTS.md` for a real bug of this class that was caught
and fixed (`current()` was accidentally swallowing `SchemaError`).
