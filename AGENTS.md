# AGENTS.md

Guide for AI agents in this repo. Read before change anything.

## What this is

`opencode-swap` = standalone Python CLI. Switches OpenCode between saved
provider accounts — securely store each account's OpenCode credential record,
atomic-swap into `~/.local/share/opencode/auth.json` — single file OpenCode
reads fresh every request. NOT OpenCode plugin. NOT runtime proxy/interceptor
(that's `opencode-balancer` project). Full rationale: `README.md` +
`docs/architecture.md`.

Model project: `claude-swap` (same idea, Claude Code). Design choices —
atomic writes, transaction+rollback, macOS Keychain routing w/ sticky file fallback
— ported+adapted here. `docs/architecture.md` + `docs/security.md` note kept
vs changed, why.

## Before touching anything

Read these two first — most "obvious" changes wrong without context:

1. `docs/opencode-auth.md` — docs OpenCode's `auth.json` format, refresh
   behavior, locking (or lack). Reverse-engineered from OpenCode source, not
   OpenCode-documented. Every assumption here re "what OpenCode does" traces
   back to this file — if OpenCode behavior in question, check/update here,
   don't re-guess.
2. `docs/architecture.md` — switch algorithm, why sync-back exists, why
   transaction/rollback only covers `auth.json` write step (not every step).
   Re-deriving from code alone easy to get subtly wrong (see "fail-safe" note
   below).

## Core invariants — do not violate

- **auth.json writes always atomic.** Temp file same dir, `chmod 0600`, then
  `os.replace`. Never write OpenCode's `auth.json` (or any file under
  opencode-swap's own data dir) other way. See `atomic.py`.
- **Never silently guess unrecognized schema.** Provider record doesn't
  match known shape → raise `SchemaError`, refuse — don't drop fields,
  don't coerce, don't proceed. OpenCode itself lenient here (silently drops
  bad entries); this project deliberately stricter, about to overwrite
  state. Real bug happened: `Switcher.current()` caught `SchemaError` via
  too-broad `except OpenCodeSwapError`, reported "no active account" instead
  of surfacing incompatibility — caught by tests, now regression-tested.
  Watch same bug class: catching specific exception's *base class* where
  should only catch specific case.
- **Never lose live credential without preserving first.** Before
  `use_account` overwrites live provider entry, either syncs managed
  account's rotated tokens back into storage, or stashes
  unmanaged/foreign login under `backups/unclaimed-*.json`. Don't add code
  path overwriting `auth.json` skipping this.
- **Secrets never touch non-secret registry.** `registry.json` holds only
  name/provider/type/account id/email/timestamp/active hints. Access tokens, refresh
  tokens, API keys only through `SecretStore` (macOS Keychain/private file). About
  to put token-shaped string into `registry.py` or `models.AccountMeta`?
  Stop.
- **No secrets in CLI output, logs, error messages.** `cli.py` truncates
  account ids to last 4 chars, never prints token fields. Anything added to
  CLI output needs same discipline — tests grep stdout/stderr for planted
  secret values.
- **No custom cryptography.** Security model = macOS Keychain first with
  `chmod 0600` obfuscated files as sticky fallback; Linux always uses those
  private files — matches (not exceeds)
  OpenCode's own trust boundary. Don't add encryption, key derivation,
  "vault" — see `docs/security.md`, deliberate choice not oversight.
- **Every mutating `Switcher` op holds `self.lock`.** Serializes
  opencode-swap's own concurrent invocations. No relation to OpenCode's own
  process — no cooperative lock protocol to join (OpenCode's own
  `auth.json` writes unlocked, non-atomic; see `docs/opencode-auth.md`).
  Don't assume lock protects against concurrently-running OpenCode;
  `process_detection.py` + confirmation prompt = best-effort advisory
  mitigation instead.

## Testing rules

- **Never let test touch real macOS Keychain.** Construct
  `Switcher`/`SecretStore` w/ `platform=Platform.UNKNOWN` (routes straight
  to file backend) or monkeypatch specific backend functions
  (`macos_keychain.*`) w/ in-memory fake. `tests/test_cli.py`'s
  `isolated_env` autouse fixture does this for all CLI tests — reuse
  pattern, don't bypass.
- **Never let test touch real `~/.local/share/opencode/auth.json`.** Point
  `Switcher(opencode_auth_path=...)` at `tmp_path`, or (CLI tests) set
  `XDG_DATA_HOME` to `tmp_path` via `monkeypatch.setenv`.
- Account names lowercased by `normalize_account_name` — using `"A"`/`"B"`
  as test account names while looking up via `"openai:A"` = bug that bit
  codebase before (fixed, easy reintroduce). Use lowercase names in tests.
- Failure-injection tests should target one specific write call, not
  monkeypatch something broad like `json.dumps` globally — multi call sites
  (`.bak` write, `auth.json` write, registry write) share same helper
  (`atomic.atomic_write_json`), global patch fails *first* one encountered,
  not necessarily one meant to test. See `test_switcher_use.py`'s
  failure-injection tests for pattern (patch specific module attribute, or
  filter by path in wrapper).
- Verify every codebase change w/ `make check`. Runs linters+tests; no
  network access, prompts, real keychain/auth files. If needs them, test
  isolation regressed.

## Project conventions

- Python 3.12+, stdlib `argparse` (no Click/Typer), dataclasses over
  ad-hoc dicts, `from __future__ import annotations` everywhere.
- `uv` for dependency mgmt (`uv sync --dev`, `uv run pytest`, `uv run
  opencode-swap ...`).
- Comments explain *why*, not *what* — esp citing specific OpenCode source
  behavior or specific incident (bug found during manual verification,
  claude-swap precedent) justifying non-obvious choice. Can't cite reason →
  comment probably shouldn't exist.
- Keep `Provider` protocol (`providers/base.py`) limited to behavior required
  by implemented providers. Static API providers use generic handling; OAuth
  requires provider-specific evidence for identity, refresh, and expiry.
- See `docs/architecture.md` for module responsibility map before adding
  new file — most new code belongs in existing module.

## Full documentation index

- `README.md` — user-facing overview, install, commands, quickstart.
- `docs/architecture.md` — module map, switch algorithm, transaction design.
- `docs/opencode-auth.md` — OpenCode's `auth.json` format/lifecycle (ground truth).
- `docs/security.md` — threat model, storage backend comparison.
- `docs/testing.md` — testing strategy in depth.
- `docs/roadmap.md` — milestone status (what's built, what's postponed).
