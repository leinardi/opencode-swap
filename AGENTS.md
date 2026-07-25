# AGENTS.md

Guide for AI coding agents working in this repository. Read this before
making changes.

## What this is

`opencode-swap` is a standalone Python CLI that switches OpenCode between
multiple saved OpenAI accounts, by securely storing each account's OpenCode
credential record and atomically swapping it into
`~/.local/share/opencode/auth.json` — the single file OpenCode itself reads
fresh on every request. It is explicitly **not** an OpenCode plugin and
**not** a runtime proxy/interceptor (that's what the separate
`opencode-balancer` project does). Full rationale in `README.md` and
`docs/architecture.md`.

Reference project this was modeled on: `claude-swap` (same idea, for Claude
Code). Its design choices — atomic writes, transaction+rollback, keychain
routing with a sticky file fallback — were deliberately ported and adapted
here; `docs/architecture.md` and `docs/security.md` note what was kept vs.
changed and why.

## Before touching anything

Read these two first — most "obvious" changes are wrong without this context:

1. `docs/opencode-auth.md` — documents OpenCode's `auth.json` format,
   refresh behavior, and locking (or lack of it). This is reverse-engineered
   from OpenCode's source, not documented by OpenCode itself. Every
   assumption in this codebase about "what OpenCode does" traces back to
   this file — if OpenCode's behavior is in question, that's where to check
   or update, not to re-guess from scratch.
2. `docs/architecture.md` — the switch algorithm, why sync-back exists, and
   why the transaction/rollback only needs to cover the `auth.json` write
   step (not every step). Re-deriving this from the code alone is easy to
   get subtly wrong (see "fail-safe" note below).

## Core invariants — do not violate

- **auth.json writes are always atomic.** Temp file in the same directory,
  `chmod 0600`, then `os.replace`. Never write OpenCode's `auth.json` (or any
  file under opencode-swap's own data dir) any other way. See `atomic.py`.
- **Never silently guess at an unrecognized schema.** If a provider record
  doesn't match a known shape, raise `SchemaError` and refuse — don't drop
  fields, don't coerce, don't proceed. OpenCode itself is lenient here (it
  silently drops bad entries); this project deliberately holds itself to a
  stricter standard because it's about to overwrite state. There was a real
  bug of exactly this kind (`Switcher.current()` was catching `SchemaError`
  via a too-broad `except OpenCodeSwapError` and reporting "no active
  account" instead of surfacing the incompatibility) — caught by tests, now
  regression-tested. Watch for the same class of bug: catching a specific
  exception's *base class* somewhere that should only catch the specific
  case.
- **Never lose a live credential without preserving it first.** Before
  `use_account` overwrites the live `openai` entry, it either syncs a
  managed account's rotated tokens back into storage, or stashes an
  unmanaged/foreign login under `backups/unclaimed-*.json`. Don't add a code
  path that overwrites `auth.json` without going through this.
- **Secrets never touch the non-secret registry.** `registry.json` holds
  only name/provider/type/account id/email/timestamp. Access tokens, refresh
  tokens, and API keys only ever go through `SecretStore` (keychain/keyring/
  file). If you're about to put a token-shaped string into `registry.py` or
  `models.AccountMeta`, stop.
- **No secrets in CLI output, logs, or error messages.** `cli.py` truncates
  account ids to the last 4 characters and never prints token fields.
  Anything you add to CLI output needs the same discipline — there are tests
  that grep stdout/stderr for planted secret values.
- **No custom cryptography.** The security model is OS keychain/keyring
  first, `chmod 0600` obfuscated files as a sticky fallback — matching (not
  exceeding) OpenCode's own trust boundary. Don't add encryption, key
  derivation, or a "vault" — see `docs/security.md` for why this was a
  deliberate choice, not an oversight.
- **Every mutating `Switcher` operation holds `self.lock`.** It serializes
  opencode-swap's own concurrent invocations. It has no relationship to
  OpenCode's own process — there's no cooperative lock protocol to join
  (OpenCode's own `auth.json` writes are unlocked and non-atomic; see
  `docs/opencode-auth.md`). Don't assume the lock protects against a
  concurrently-running OpenCode; `process_detection.py` + a confirmation
  prompt is the (best-effort, advisory) mitigation for that instead.

## Testing rules

- **Never let a test touch the real OS keychain/keyring.** Construct
  `Switcher`/`SecretStore` with `platform=Platform.UNKNOWN` (routes straight
  to the file backend) or monkeypatch the specific backend functions
  (`macos_keychain.*`, `keyring.*`) with an in-memory fake. `tests/test_cli.py`'s
  `isolated_env` autouse fixture does this for all CLI tests — reuse that
  pattern, don't bypass it.
- **Never let a test touch the real `~/.local/share/opencode/auth.json`.**
  Point `Switcher(opencode_auth_path=...)` at a `tmp_path`, or (for CLI
  tests) set `XDG_DATA_HOME` to a `tmp_path` via `monkeypatch.setenv`.
- Account names get lowercased by `normalize_account_name` — using `"A"`/`"B"`
  as test account names while looking them up with `"openai:A"` is a bug
  that has bitten this codebase before (fixed, but easy to reintroduce).
  Use lowercase names in tests.
- Failure-injection tests should target one specific write call, not
  monkeypatch something broad like `json.dumps` globally — multiple call
  sites (`.bak` write, `auth.json` write, registry write) share the same
  helper (`atomic.atomic_write_json`), so a global patch fails the *first*
  one encountered, not necessarily the one you meant to test. See
  `test_switcher_use.py`'s failure-injection tests for the pattern (patch a
  specific module attribute, or filter by path in a wrapper).
- Run `uv run pytest -q` — should be ~167+ tests, a couple seconds, no
  network access, no prompts. If it's slower or needs network/keychain
  access, something regressed the isolation above.

## Project conventions

- Python 3.12+, stdlib `argparse` (no Click/Typer), dataclasses over
  ad-hoc dicts, `from __future__ import annotations` everywhere.
- `uv` for dependency management (`uv sync --dev`, `uv run pytest`, `uv run
  opencode-swap ...`).
- Comments explain *why*, not *what* — especially citing the specific
  OpenCode source behavior or the specific incident (a bug found during
  manual verification, a claude-swap precedent) that justifies a non-obvious
  choice. If you can't cite a reason, the comment probably shouldn't exist.
- Don't add abstraction for a hypothetical second provider "in case it's
  needed later" — the `Provider` protocol (`providers/base.py`) is
  deliberately the smallest seam that makes adding provider #2 not require
  touching `switcher.py`. Keep it that way; don't grow it speculatively.
- See `docs/architecture.md` for the module responsibility map before adding
  a new file — most new code belongs in an existing module.

## Full documentation index

- `README.md` — user-facing overview, install, commands, quickstart.
- `docs/architecture.md` — module map, switch algorithm, transaction design.
- `docs/opencode-auth.md` — OpenCode's `auth.json` format/lifecycle (ground truth).
- `docs/security.md` — threat model, storage backend comparison.
- `docs/testing.md` — testing strategy in depth.
- `docs/roadmap.md` — milestone status (what's built, what's postponed).
