# opencode-swap

**Multi-account switcher for [OpenCode](https://github.com/anomalyco/opencode)** — the
same idea as [claude-swap](https://github.com/realiti4/claude-swap), applied to
OpenCode's OpenAI (ChatGPT) accounts.

OpenCode has no built-in way to keep several OpenAI accounts around and flip
between them. `opencode-swap` is a small standalone CLI that does exactly
that: it keeps a securely-stored copy of each account's OpenCode auth state
and swaps the one OpenCode currently considers "active."

```bash
opencode-swap use personal && opencode
```

That's the whole workflow. No OpenCode plugin, no background process, no
proxy — `opencode-swap` isn't running while OpenCode is.

## Status

Early / self-hosted. The core is implemented and tested (280 tests) and has
been exercised against a real OpenCode installation, but it isn't packaged
or released yet — run it from a local checkout with `uv`. See
[`docs/roadmap.md`](docs/roadmap.md) for what's done and what's left.

v1 scope: **OpenAI accounts only**, on **Linux and macOS**. See
[Scope](#scope) below.

## Why not `opencode-balancer`?

[`opencode-balancer`](https://github.com/thelioo/opencode-balancer) solves
a related but different problem: it's an OpenCode *plugin* that load-balances
requests across multiple accounts at runtime, and it stores credentials in a
plaintext SQLite database. `opencode-swap` is a different shape entirely — a
standalone binary that swaps *which single account* is active, with
credentials protected by your OS keychain/keyring rather than a plaintext
file. See [`docs/security.md`](docs/security.md) for the full comparison and
threat model.

## Install

Not published yet. Run from a checkout:

```bash
git clone <this-repo>
cd opencode-swap
uv sync
uv run opencode-swap --help
```

`uv tool install .` from the checkout also works if you want `opencode-swap`
and `ocs` on your `PATH`.

## Quickstart

```bash
# 1. Log into OpenCode normally with your first account.
opencode auth login

# 2. Import whichever account is currently active into opencode-swap.
opencode-swap add personal

# 3. Log into a second account.
opencode auth login   # choose/authenticate a different ChatGPT account
opencode-swap add work

# 4. See what's saved.
opencode-swap list
#   personal   ...abcd   -
# * work       ...ef01   -

# 5. Switch whenever you like.
opencode-swap use personal
opencode
```

Names are yours to choose (lowercase letters/digits/`-`/`_`/`.`). Re-running
`add` with the name of an already-imported account just refreshes its stored
tokens in place — it won't create a duplicate.

## Commands

| Command | Description |
| --- | --- |
| `opencode-swap add <name>` | Import the currently-active OpenAI account into secure storage under `<name>`. |
| `opencode-swap list` | List saved accounts, with an active marker, truncated account id, and expiry/validity flag. |
| `opencode-swap current` | Show which saved account (if any) OpenCode is currently using. |
| `opencode-swap use <name> [-y]` | Switch OpenCode's active OpenAI account to `<name>`. |
| `opencode-swap switch [-y]` | Switch to next saved OpenAI account, wrapping around after the last account. |
| `opencode-swap remove <name> [-y]` | Delete a saved account (from both the registry and the secret store). |
| `opencode-swap rename <old> <new>` | Rename a saved account. |
| `opencode-swap export <path>` | Export all saved accounts to a new password-encrypted `.ocs` archive. |
| `opencode-swap import <path>` | Import accounts from an encrypted archive; prompts to skip or overwrite existing names. |
| `opencode-swap restore [--pristine] [-y]` | Recover `auth.json` from the most recent pre-switch backup, or from the very first snapshot ever taken (`--pristine`). |
| `opencode-swap doctor` | Diagnose paths, schema compatibility, secret backend, and backup state. |

`use`, `switch`, `remove`, and `restore` ask for confirmation unless you pass `-y`/`--yes`
(and refuse to prompt at all on a non-interactive terminal — pass `-y` in
scripts). `use` also warns if it detects a running `opencode` process, since
switching while OpenCode might be mid-token-refresh is the one real race
condition this tool can't fully close (see
[`docs/security.md`](docs/security.md)).

No command ever prints an access token, refresh token, or API key. Account
ids are shown truncated to their last four characters.

### Moving accounts to another computer

```bash
# Source computer
opencode-swap export ~/opencode-accounts.ocs

# Transfer the archive, then on the destination computer
opencode-swap import ~/opencode-accounts.ocs
opencode-swap use personal
```

`export` asks for a password twice; `import` asks for it once. Password input
requires an interactive terminal and is never placed in command arguments or
printed. The archive is AES-256 encrypted and created with `0600` permissions.
When an export path has no extension, `export` asks whether to append `.ocs`
(default: yes). Repositories using this project's `.gitignore` ignore `*.ocs`
to reduce risk of accidentally committing an account archive.
Import validates every account and checks all destination names and identities
before writing anything. For existing account names, it offers `skip`,
`skip-all`, `overwrite`, `overwrite-all`, and `abort`. Identity conflicts under
different names still abort because there is no unambiguous overwrite target.
Use the shortcuts `s`, `sa`, `o`, `oa`, and `a`, respectively, at the prompt.
Imported accounts go through the normal secret backend (macOS Keychain, Linux
keyring, or file fallback), while the destination's active account and OpenCode
`auth.json` remain unchanged. Delete the transfer archive after successful
import. Use a strong, unique archive passphrase; archive security depends on
its entropy. Export refuses if the live credential cannot be proven to match
the registry-active account, rather than risk archiving stale rotated tokens.

## How it works

The short version: OpenCode keeps all of its provider credentials in a
single JSON file, `~/.local/share/opencode/auth.json`, and re-reads it fresh
on every request — no restart required to pick up a change. The entire unit
of "which OpenAI account is active" is the `"openai"` key of that file.
`opencode-swap` keeps a copy of each account's record in your OS
keychain/keyring, and `use <name>` atomically replaces that one key.

The interesting part is what happens *between* switches: OpenCode rotates
the access and refresh token in place whenever it refreshes, so a naively
cached copy would go stale. `opencode-swap` captures that rotation back into
its own storage every time you switch *away* from an account, before
overwriting it with another.

Full details, including the exact OpenCode internals this was reverse
engineered from and the switch algorithm's failure-recovery guarantees, are
in [`docs/opencode-auth.md`](docs/opencode-auth.md) and
[`docs/architecture.md`](docs/architecture.md).

## Security

Credentials are stored via:

- **macOS** — the system Keychain, through the pinned `/usr/bin/security` CLI.
- **Linux** — the OS keyring (Secret Service / libsecret) via the `keyring`
  library, when available.
- **Fallback** (headless Linux, no keyring) — `chmod 0600` files, obfuscated
  (base64) but not encrypted — the same posture OpenCode's own `auth.json`
  already has.

No custom cryptographic protocol. Portable exports use standard WinZip AES-256
implemented by `pyzipper`; plaintext credentials never touch a temporary file.
No plaintext database. Full threat model in
[`docs/security.md`](docs/security.md).

## Scope

**In scope (v1):** standalone CLI, Linux + macOS, OpenCode's OpenAI/ChatGPT
accounts, secure multi-account storage, transactional switching with
rollback, backup/recovery, an architecture that doesn't need a rewrite to
add a second provider later.

**Out of scope:** being an OpenCode plugin, load balancing / round-robin,
runtime request interception, a local proxy, Windows, providers other than
OpenAI (for now), reimplementing or modifying OpenCode, cloud sync, a GUI/TUI.

The mental model is **"claude-swap, but for OpenCode"** — not a replacement
for `opencode-balancer`.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — module map, data flow, the switch algorithm, transaction/rollback design.
- [`docs/opencode-auth.md`](docs/opencode-auth.md) — how OpenCode itself stores and refreshes OpenAI credentials (the reverse-engineered ground truth this tool depends on).
- [`docs/security.md`](docs/security.md) — threat model, storage mechanism comparison, what's explicitly out of scope.
- [`docs/testing.md`](docs/testing.md) — testing strategy and how to run the suite.
- [`docs/roadmap.md`](docs/roadmap.md) — milestone status.
- [`AGENTS.md`](AGENTS.md) — project guide for AI coding agents working in this repo.

## Development

```bash
make python-sync
make python-test          # No network/keychain access required
make doctor
```

Run `make help` for all development targets. Direct `uv` commands remain
available when Make is not installed.

See [`docs/testing.md`](docs/testing.md) for what the suite covers and how
it keeps real OS keychains/keyrings and your real `auth.json` out of the
loop, and [`AGENTS.md`](AGENTS.md) for conventions to follow when changing
code here.

## License

MIT
