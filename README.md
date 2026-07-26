# opencode-swap

**Multi-account switcher for [OpenCode](https://github.com/anomalyco/opencode)** — the
same idea as [claude-swap](https://github.com/realiti4/claude-swap), applied to
OpenCode provider accounts.

OpenCode has no built-in way to keep several accounts for one provider around and flip
between them. `opencode-swap` is a small standalone CLI that does exactly
that: it keeps a securely-stored copy of each account's OpenCode auth state
and swaps the one OpenCode currently considers "active."

```bash
opencode-swap use openai personal && opencode
```

Core workflow stays standalone: no background process or proxy runs while
OpenCode is. An optional terminal UI plugin adds account status and commands
without taking ownership of credentials or swaps.

## Status

Early / self-hosted. Core behavior is covered by automated tests and has
been exercised against a real OpenCode installation, but it isn't packaged
or released yet — run it from a local checkout with `uv`. See
[`docs/roadmap.md`](docs/roadmap.md) for what's done and what's left.

**OpenAI is the only provider tested end to end with real accounts.** Other
provider implementations were derived from OpenCode source and synthetic tests.
Maintainer does not have accounts for those services, so testers are wanted for
every non-OpenAI provider. Please report provider, auth method, OpenCode version,
and sanitized failure details in an issue. Never include credentials. Use
[`docs/provider-research-prompt.md`](docs/provider-research-prompt.md) for a
copy-paste OpenCode prompt that gathers safe source evidence for one provider.

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
opencode-swap add openai personal

# 3. Log into a second account.
opencode auth login   # choose/authenticate a different ChatGPT account
opencode-swap add openai work

# 4. See what's saved.
opencode-swap list
#   personal   ...abcd   -
# * work       ...ef01   -

# 5. Switch whenever you like.
opencode-swap use openai personal
opencode
```

Names are yours to choose (lowercase letters/digits/`-`/`_`/`.`). Re-running
`add` with the provider and name of an already-imported account refreshes its stored
tokens in place — it won't create a duplicate.

## Commands

| Command | Description |
| --- | --- |
| `opencode-swap add <provider> <name>` | Import the provider's currently-active account under `<name>`. |
| `opencode-swap list [provider]` | List every saved account, or filter by provider. |
| `opencode-swap current [provider]` | Show active managed accounts for every provider, or one provider. |
| `opencode-swap status [provider] [--json] [--usage]` | Show integration status; `--json` emits versioned secret-safe data. |
| `opencode-swap use <provider> <name> [-y]` | Activate one saved provider account. |
| `opencode-swap switch <provider> [-y]` | Switch to next saved account for provider. |
| `opencode-swap remove <provider> <name> [-y]` | Delete a saved provider account. |
| `opencode-swap rename <provider> <old> <new>` | Rename a saved provider account. |
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

## OpenCode TUI integration

[`integrations/opencode-tui-plugin`](integrations/opencode-tui-plugin) uses
OpenCode's supported TUI-plugin API to render active account and active-only
usage at right side of session prompt metadata:

```text
Plan · GPT-5.6 Sol OpenAI · medium                         work · 17%
```

It only appears after session has sent a request through provider managed by
`opencode-swap`; it stays hidden for unrelated providers. `/swap` opens a
safe account picker, `/swap-next` rotates current provider, and `/swap-refresh`
refreshes visible status. See the
[plugin README](integrations/opencode-tui-plugin/README.md) for install and
concurrency limits.

Core CLI does not require Bun. TUI plugin development and root `make verify`
require Bun 1.3.14; `make check` remains Python-only and offline.

### Moving accounts to another computer

```bash
# Source computer
opencode-swap export ~/opencode-accounts.ocs

# Transfer the archive, then on the destination computer
opencode-swap import ~/opencode-accounts.ocs
opencode-swap use openai personal
```

`export` asks for a password twice; `import` asks for it once. Password input
requires an interactive terminal and is never placed in command arguments or
printed. The archive is AES-256 encrypted and created with `0600` permissions.
When an export path has no extension, `export` asks whether to append `.ocs`
(default: yes). Repositories using this project's `.gitignore` ignore `*.ocs`
to reduce risk of accidentally committing an account archive.
Import validates every account and checks all destination provider/name pairs and identities
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
of "which account is active" is one top-level provider key in that file.
`opencode-swap` keeps a copy of each account's record in your OS
keychain/keyring, and `use <provider> <name>` atomically replaces that one key.

The interesting part is what happens *between* switches: OpenCode rotates
the access and refresh token in place whenever it refreshes, so a naively
cached copy would go stale. `opencode-swap` captures that rotation back into
its own storage every time you switch *away* from an account, before
overwriting it with another. Providers using static API keys do not rotate
credentials, but use the same atomic switching path.

### Provider support

| Provider/auth class | Status |
| --- | --- |
| OpenAI API key and ChatGPT OAuth | Supported; only end-to-end tested provider |
| Any provider using OpenCode's canonical `{"type":"api","key":"..."}` record | Supported generically; testers wanted |
| GitHub Copilot OAuth | Supported from OpenCode source analysis; testers wanted |
| Poe API/OAuth | Supported from pinned plugin source analysis; testers wanted |
| xAI API/OAuth | API supported; OAuth requires access JWT with stable `iss`/`sub`; testers wanted |
| GitLab and Snowflake API/PAT | Supported through generic API handling; testers wanted |
| GitLab and Snowflake OAuth | Not supported: OpenCode does not persist a provably stable per-user identity |
| Well-known URL auth and unknown OAuth plugins | Not supported |

Same account name may exist under different providers. `openai:work` and
`anthropic:work` are separate accounts. Provider IDs must match keys in
OpenCode's `auth.json` exactly.

Upgrading an existing installation automatically migrates registry v1 to
provider-scoped registry v2 while holding opencode-swap's lock. Existing
keychain/keyring entries are not rewritten because their keys were already
`<provider>:<name>`. Original registry is retained as
`registry.v1.json.bak`; publication of v2 registry is atomic.

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

**In scope:** standalone CLI, Linux + macOS, OpenCode provider accounts with
known safe identity semantics, secure multi-account storage, transactional switching with
rollback, backup/recovery, an architecture that doesn't need a rewrite to
add provider-specific behavior without changing safe I/O machinery.

**Out of scope:** being an OpenCode plugin, load balancing / round-robin,
runtime request interception, a local proxy, Windows, unsupported/ambiguous
OAuth providers, reimplementing or modifying OpenCode, cloud sync, a GUI/TUI.

The mental model is **"claude-swap, but for OpenCode"** — not a replacement
for `opencode-balancer`.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — module map, data flow, the switch algorithm, transaction/rollback design.
- [`docs/opencode-auth.md`](docs/opencode-auth.md) — how OpenCode stores provider credentials and refreshes OpenAI credentials.
- [`docs/provider-support.md`](docs/provider-support.md) — provider matrix, source evidence, live-testing status, and deferred OAuth cases.
- [`docs/provider-research-prompt.md`](docs/provider-research-prompt.md) — safe copy-paste prompt for provider-research issues.
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
