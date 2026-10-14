# opencode-swap

**Account usage and one-step account switching for [OpenCode](https://github.com/anomalyco/opencode) 2.**

OpenCode 2 stores several accounts per provider itself and switches between them with
`/connect` or `opencode auth switch`. `@leinardi/opencode-swap` is a TUI plugin that adds what
it lacks:

- **A footer** under the prompt with the session provider's active account and, for ChatGPT
  and z.ai GLM Coding Plan accounts, the usage of every quota window:

  ```text
  work · 5h 42% @14:30 | 7d 10% @Oct 18, 09:00
  ```

- **`/swap`**: one picker for every stored account of every provider, with its label, its
  email or key hint, and its usage.
- **`/swap-next`**: the next account of the current session's provider.
- **`/swap-refresh`**: reload accounts and usage now.

It is a plugin only: no background process, no proxy, no credential store of its own. It reads
the credentials OpenCode already stores and switches which one is active, and nothing else.

> **Coming from 0.4.x?** 0.4.x was a Python CLI with its own credential vault, for OpenCode
> 1.x. It is retired at 0.4.7. See [Coming from 0.4.x](#coming-from-04x) to move your saved
> accounts into OpenCode 2, and [OpenCode 1.x](#opencode-1x) to keep the old plugin.

🤖 This project is built largely with agentic AI coding tools, under human review. Read the
code before trusting it with your credentials.

## Requirements

OpenCode 2.0.20 or later. Older 2.0.x releases lack the credential API the plugin uses: there
it shows one toast, "opencode-swap needs OpenCode 2.0.20 or later.", and registers nothing.

Tested, with fake credentials in an isolated sandbox, on OpenCode 2.0.20 and 2.0.29. Usage
numbers from real ChatGPT and z.ai accounts are still to be confirmed (see
[Network access](#network-access)).

## Install

```bash
opencode plugin add @leinardi/opencode-swap
```

This installs the package and adds it to `"plugins"` in OpenCode's `cli.json`
(`~/.config/opencode/cli.json`). Restart the TUI to load it. To add it by hand, or to pass
[options](#options), list it there yourself:

```json
{
  "plugins": [
    { "package": "@leinardi/opencode-swap", "options": { "usage": false } }
  ]
}
```

### Updating

OpenCode installs a plugin once and never refreshes it on its own: a new release reaches you
only when you ask for it.

```bash
opencode plugin update
```

Restart the TUI afterwards. `opencode plugin check` shows whether an update is available.
OpenCode's `/plugins` dialog offers update (`ctrl+u`) only for server plugins, so it does not
update this one.

## Commands

All three are slash commands and palette entries, under "Accounts".

| Command | What it does |
| --- | --- |
| `/swap` | Lists every stored account, grouped by provider. Each row shows the label, the account's email (OpenAI) or the last 4 characters of its key, and `active` or its usage. Picking an account and confirming makes it the provider's active account. |
| `/swap-next` | Switches the current session's provider to its next stored account, in label order, wrapping around. Asks for confirmation. |
| `/swap-refresh` | Reloads the stored accounts and looks up usage again, past the cache. |

What a switch does, and does not do:

- **It changes which credential the provider uses**, exactly like `opencode auth switch`. Every
  session using that provider sends its next request with the new account.
- **It never changes a session's model or provider.** Switching another provider's account
  from `/swap` leaves the current session alone, and the toast says so.
- **The confirmation warns when the current session has a request in progress** on that
  provider.
- **Cancelling either dialog changes nothing.** If OpenCode refuses the switch, an error toast
  says so and the active account stays as it was.

### The footer

The footer follows the current session's provider: the session's model once it has one, else
the last model it used, else the model selected in the prompt. It shows that provider's active
stored account:

- `label` alone for an account without a usage source (see [Network access](#network-access)),
  or with usage turned off;
- `label · …` while usage is being looked up;
- `label · n/a` when the lookup failed or was refused;
- `label · 5h 42% @14:30 | 7d 10% @Oct 18, 09:00` with one entry per quota window the provider
  reports, labelled by the window's own length, with its reset time.

Each window is coloured by pace when its length and reset time are known: usage compared with
the share of the window already gone. Green below 85% of that pace (and always in the first 5%
of the window), orange below 105%, red from 105%. Otherwise by absolute usage: green below
50%, then yellow, orange from 70%, red from 90%.

The footer is hidden when the provider has no stored account, and when an environment variable
(such as `OPENAI_API_KEY`) is what OpenCode uses for it, since the stored label would then name
the wrong credential.

Accounts and usage refresh every 60 seconds, when the session's provider changes, when
OpenCode reports an account switched or edited, and on `/swap-refresh`. A usage result is
reused for up to 55 seconds. After a 401 or 403 the plugin stops asking for that account's
usage until its credential changes or you run `/swap-refresh`.

## Options

Plugin options go in the `options` object of the `cli.json` entry.

| Option | Default | Effect |
| --- | --- | --- |
| `usage` | `true` | `false` turns every usage lookup off: the footer then shows only the label and the plugin makes no network request at all. `0`, `""`, `null` and the strings `"false"`, `"no"`, `"off"` and `"0"` also turn it off. |
| `debug` | `false` | `true` shows why the footer is hidden (`swap · openai: env connection`) instead of nothing. Only the exact boolean `true` turns it on. |

## Network access

With `usage` on, the plugin sends **an account's own credential** to its provider's usage
endpoint, and nowhere else:

| Account | Request | Credential sent |
| --- | --- | --- |
| OpenAI OAuth (ChatGPT login) | `GET https://chatgpt.com/backend-api/wham/usage` | the OAuth access token as `Bearer`, and its ChatGPT account id when the token carries one |
| z.ai `zai-coding-plan` API key | `GET https://api.z.ai/api/monitor/usage/quota/limit` | the API key as `Bearer` |

Every other provider and credential type makes no request. Redirects are not followed, so a
credential never reaches another host. The footer looks up the session provider's active
account; `/swap` looks up every eligible account before it opens (at most 4 at a time, for at
most 5 seconds; slower ones show `…`).

The plugin never refreshes a token or calls a token endpoint: an expired access token shows as
`n/a` until OpenCode itself refreshes it on its next request.

OpenCode 2's default ChatGPT login stores a `chatgpt-token-sharing` credential. Whether
`wham/usage` accepts those tokens has not been checked with a real login yet; until it is, if
it does not, those accounts show `n/a`.

## Coming from 0.4.x

0.4.x kept each account in its own vault and swapped it into OpenCode 1's `auth.json`.
OpenCode 2 stores accounts itself and no longer reads `auth.json`, so the vault has nothing to
switch. The last CLI release, 0.4.7, moves the vault's accounts into OpenCode 2:

```bash
# Quit OpenCode 1 first. With OpenCode 2.0.20 or newer installed:
opencode-swap migrate-to-opencode2 | opencode auth import
```

- Each saved account becomes one OpenCode 2 credential, labelled with its name.
- An account OpenCode 1 is logged into is exported from `auth.json`, which holds its latest
  refreshed token.
- Accounts OpenCode 2 already has are left out, including the one it imports from `auth.json`
  on its first start, so re-running is safe.
- It refuses, writing nothing, rather than guess: when it cannot tell which account OpenCode 1's
  login belongs to, or when an id it would create already exists holding something else.

The CLI's own README on PyPI (`opencode-swap` 0.4.7) has the details and every option. Then:

1. [Install](#install) this plugin in OpenCode 2.
2. Switch accounts in OpenCode 2 from then on, not with the 0.4.x CLI. The vault and OpenCode 2
   now hold the same refresh tokens, and a refresh by either side spends the other's copy.
3. Once everything works in OpenCode 2, remove the saved accounts with
   `opencode-swap remove <provider> <name>`, which also clears their macOS Keychain entries, and
   uninstall the CLI (`uv tool uninstall opencode-swap` or `pipx uninstall opencode-swap`).

What 0.4.x did that OpenCode 2 now does itself:

| 0.4.x | OpenCode 2 |
| --- | --- |
| `opencode-swap add`, `remove`, `rename` | `/connect`, `opencode auth login`, `opencode auth logout` |
| `opencode-swap use`, `switch` | `/swap`, `/swap-next`, `opencode auth switch` |
| `opencode-swap list`, `current`, `status` | `/swap`, `opencode auth list` |
| `opencode-swap export`, `import` | `opencode auth export`, `opencode auth import` |
| `opencode-swap list --usage` | the footer and `/swap` |

## OpenCode 1.x

1.0.0 and later need OpenCode 2. OpenCode 1 refuses to load them (`"engines": {"opencode":
">=2.0.20 <3"}`), and it resolves a bare package name to the newest release. Pin the 0.4.x line
in OpenCode 1's `tui.json`:

```json
{
  "plugin": ["@leinardi/opencode-swap@0.4"]
}
```

The 0.4.x plugin needs the 0.4.x CLI (`uv tool install opencode-swap`). Both are frozen: the
CLI gets no further releases, and the plugin only fixes on the `maint/0.4` branch, published
under npm's `legacy` tag.

## Looking for load balancing?

`opencode-swap` switches the one active account. If you want requests spread across several
accounts at runtime, [`opencode-balancer`](https://github.com/thelioo/opencode-balancer) is the
project for it.

## Scope

**In scope:** showing the active account and its usage in OpenCode 2's TUI, switching which
stored credential a provider uses, and usage lookups for providers with a usage endpoint.

**Out of scope:** storing, editing, refreshing or exporting credentials (OpenCode 2 does all of
that), load balancing or round-robin, a runtime proxy, and changing OpenCode's own behaviour.

## Security

The plugin stores nothing. It reads stored credentials through OpenCode's plugin API, keeps a
credential's value only for the one usage request that needs it, and never writes, refreshes,
creates or removes a credential: the only change it makes is `credential.activate`. No token,
key or JWT payload appears in the footer, a dialog, a toast or a log; the one visible fragment
is a key's last 4 characters, and only for keys of at least 20 characters.

The threat model is in
[`docs/security.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/security.md).
Report a vulnerability as described in
[`SECURITY.md`](https://github.com/leinardi/opencode-swap/blob/main/SECURITY.md).

## Documentation

- [`docs/architecture.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/architecture.md):
  module map, data flow, the usage cache and request ownership.
- [`docs/opencode-v2-integration.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/opencode-v2-integration.md):
  OpenCode 2 behaviour the plugin relies on, from source and sandbox runs.
- [`docs/provider-support.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/provider-support.md):
  what the plugin shows for each provider and credential type.
- [`docs/security.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/security.md):
  threat model and network calls.
- [`docs/testing.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/testing.md):
  the test suite and the OpenCode sandbox.
- [`docs/releasing.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/releasing.md):
  cutting a release, and the `maint/0.4` line.
- [`CONTRIBUTING.md`](https://github.com/leinardi/opencode-swap/blob/main/CONTRIBUTING.md):
  setting up, testing and submitting changes.
- [`AGENTS.md`](https://github.com/leinardi/opencode-swap/blob/main/AGENTS.md): guide for AI
  coding agents working in this repo.

## Development

Needs [Bun](https://bun.sh) 1.3.14, plus Python for the pre-commit hooks.

```bash
make plugin-sync      # install locked dependencies
make plugin-test      # unit tests: no network, no OpenCode
make verify           # everything CI runs
```

Try a change in a real OpenCode only through the isolated sandbox, `scripts/oc2-sandbox.sh`,
never your own installation; see
[`docs/testing.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/testing.md).

## License

MIT
