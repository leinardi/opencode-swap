# Testing

```bash
make python-sync
make python-test
```

The equivalent direct command is `uv run pytest -q`.

184 tests, runs in ~2 seconds. No network access, no real OS
keychain/keyring access, and no access to your real
`~/.local/share/opencode/auth.json` is required or performed by the suite.

## Isolation

Two things must never happen in a test, and the fixtures exist specifically
to prevent them:

- **Touching the real OS keychain/keyring.** `Switcher`/`SecretStore` accept
  a `platform` override; passing `platform=Platform.UNKNOWN` routes straight
  to the file backend. CLI tests use an autouse fixture
  (`tests/test_cli.py::isolated_env`) that monkeypatches
  `Platform.detect` for the same effect, so every `cli.main(...)` call in
  the suite is isolated by default.
- **Touching the real `auth.json`.** Non-CLI tests construct `Switcher`
  with `opencode_auth_path` pointed at a `tmp_path`. CLI tests set
  `XDG_DATA_HOME` to a `tmp_path` via `monkeypatch.setenv`, which
  `paths.py` honors the same way it would honor a real environment
  variable.

If a new test needs either of these and doesn't go through the existing
patterns, that's a bug in the test, not a reason to add a new pattern.

## What's covered, by category

**Unit** — path resolution (`XDG_DATA_HOME`, `OPENCODE_TEST_HOME`
awareness), JWT claim decoding and its fallback chains, provider
extract/splice/identity/describe/validate for all three OpenAI record types
(oauth/api/wellknown) and their malformed variants, atomic write primitives
(including permission bits and no-leftover-temp-file assertions), the
account-name normalization rules, `FileLock` acquire/release/timeout,
`SecretStore` backend routing (including file-wins-on-read and
reconcile-on-write), and `Registry` CRUD.

**Integration** — the full `add` → `add` (second account) → `use` → `use` →
`use` round trip, asserting the *exact* resulting `auth.json` content at
each step, byte-for-byte against what's in the secret store. A dedicated
rotation test simulates OpenCode refreshing a token while an account is
live, then asserts the next switch-away captures the rotated (not stale)
refresh token, and a later reactivation of that account carries the
captured version — this is the test for the R1 risk described in
`docs/architecture.md`.

**Failure injection** — for `use_account`, a failure is injected at each of:
before the `.bak` write, during the sync-back secret-store write, during
`auth.json`'s own temp-file write, immediately before the atomic rename, and
after the rename succeeds (forcing the registry write to fail, which
exercises the rollback path). Each asserts the live `auth.json` is either
completely untouched or correctly rolled back, and that no temp files are
left behind.

**Concurrency** — `tests/test_locking.py` proves real mutual exclusion under
five-thread contention on the same lock file (never more than one holder at
a time — this exercises the actual `flock()` kernel primitive, which is
per-open-file-description regardless of whether the contenders are threads
or processes). `tests/test_concurrency.py` races two, then ten, independent
`Switcher` instances against `use_account` at the same time and asserts the
final `auth.json` is always valid, complete JSON matching exactly one
account — never corrupted, truncated, or a blend of two writes.

**Compatibility** — malformed/unrecognized provider records raise
`SchemaError` from `add_account`, `use_account`, and `current()` alike, with
no partial side effects (no registry entry created, no secret stored, live
`auth.json` untouched). This category caught a real bug during development:
`current()` was catching the generic `OpenCodeSwapError` base class, which
also (silently) caught `SchemaError` since it's a subclass — masking a real
schema incompatibility as "no active account." Fixed and regression-tested;
see `AGENTS.md` for the general lesson (watch for exception handling that's
broader than intended).

**Security** — every secret/backup/registry/temp file created during tests
is asserted to be `0600` (dirs `0700`); a planted secret value is asserted
to never appear in captured CLI stdout/stderr across `add`/`list`
(the two commands that display account data); the macOS Keychain wrapper's
secret-passing-via-stdin behavior is exercised through mocked
`subprocess.run` calls rather than a real Keychain.

## Live verification (not part of the automated suite)

Several milestones were additionally verified against a real OpenCode
installation and real account during development — a real self-switch
(`use_account` on the already-active account, provably a no-op), a real
`add_account` against a live OAuth token, and a real synthetic-account swap
that triggered an actual OpenCode request and observed it use the swapped
(fake) credential. These aren't automated (they require a live OpenCode
install and, for the multi-account case, two real ChatGPT accounts) but are
what caught both permission bugs described below — worth repeating manually
after any change to `switcher.py`, `store.py`, or `atomic.py`.

Two real bugs were found this way, not by the automated suite alone, and
both are now covered by regression tests: the `opencode-swap` data
directory and its `backups/` subdirectory were initially created at
whatever the process umask left them at (`775` in the environment they were
found in) instead of the required `0700` — files inside were correctly
`0600` throughout, but the directories themselves were listable by other
users in the same group. Both are now explicitly `chmod`'d and both have
dedicated permission-assertion tests.
