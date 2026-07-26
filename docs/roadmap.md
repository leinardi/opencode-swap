# Roadmap / milestone status

Milestone numbering follows the original feasibility study and
implementation plan.

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 — Technical spike | Done | Proved the core premise against a real OpenCode install: swapping `auth.json`'s `"openai"` key is picked up on the next request with no restart. |
| M1 — Project skeleton | Done | `pyproject.toml`, entrypoints, `paths.py`, `models.py`. |
| M2 — OpenCode auth discovery | Done | `opencode_auth.py`, `providers/openai.py`, `oauth_jwt.py`; verified against a real `auth.json`. |
| M3 — Secure account storage | Done | `store.py` (keychain/keyring/file routing, sticky fallback), `macos_keychain.py`, `locking.py`; verified against a real Linux Secret Service keyring. |
| M4 — OpenAI account import | Done | `Switcher.add_account` — identity dedup, refresh-in-place on re-add. |
| M5 — Safe account switching | Done | `Switcher.use_account` — sync-back, transaction/rollback, full failure-injection test matrix. |
| M6 — CLI UX | Done | `add`/`list`/`current`/`use`/`remove`/`rename`/`export`/`import`/`doctor`. |
| M7 — Recovery and compatibility | Done | `restore` command (`.bak`/`--pristine`), `doctor` schema/backup reporting. |
| M8 — Testing | Done | Unit, integration, failure-injection, real multi-thread concurrency, compatibility, and security test categories all covered — see `docs/testing.md`. |
| M9 — Packaging/release | **Postponed** | Not published to PyPI. Runs from a local checkout via `uv`. |
| M10 — Portable transfer | Done | Password-encrypted all-account export/import with strict conflict preflight and no live `auth.json` changes. |

## Current state

280 automated tests, plus several rounds of live verification against a
real OpenCode installation and real OpenAI account (see
`docs/testing.md#live-verification`). Two real permission bugs were found
and fixed during that live verification (data directory and backups
directory not locked down to `0700`) — both are now regression-tested.

## Known gaps / deliberately deferred

- **No standalone OAuth refresh.** `opencode-swap` never refreshes an
  expired token itself; it relies on OpenCode refreshing on the next
  request after a switch. This was a deliberate v1 decision — a standalone
  refresh would add coupling to OpenCode's private `client_id` without
  removing the need for sync-back (which is the part that actually matters;
  see `docs/architecture.md#why-sync-back-is-mandatory`). Could be
  reconsidered later if fail-fast validation on `add`/`use` (detecting a
  dead refresh token before activating an account) turns out to be worth
  the added coupling.
- **No Windows support.** Out of scope for v1; `locking.py` is POSIX-only
  (`fcntl`), and the storage backend comparison in `docs/security.md` was
  only evaluated for Linux/macOS.
- **No providers besides OpenAI.** The `Provider` seam
  (`docs/architecture.md#the-provider-seam`) exists specifically so this is
  additive later, but no second provider has been built or planned yet.
- **`process_detection.py` is best-effort.** It shells out to `pgrep -x
  opencode`; if `pgrep` is unavailable it silently reports "not running"
  rather than failing the command. This is advisory, not a safety
  guarantee — see `docs/security.md#the-one-residual-race-and-why-its-acceptable`.
- **No packaging/release** (M9). No PyPI package, no `uv tool install
  opencode-swap` from a registry, no self-update mechanism. Explicitly
  postponed by request, not blocked technically.
