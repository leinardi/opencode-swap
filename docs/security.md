# Security

The plugin runs inside OpenCode 2's TUI process with OpenCode's own access to every stored
credential: `client.credential.list()` returns OAuth tokens and API keys in full (see
[`opencode-v2-integration.md`](opencode-v2-integration.md), `V7`). Its job is to use that access
for as little as possible and to show none of it.

## Threat model

### In scope

- Credential values in anything the plugin shows or emits: the footer, dialogs, toasts, thrown
  errors, logs, and the plugin's exposed state.
- Credential values sent anywhere other than the account's own usage endpoint.
- A response, redirect or error message that would carry a credential back into what the
  plugin shows.
- The plugin changing credentials beyond what the user asked: creating, editing, removing or
  refreshing one, or switching without confirmation.
- A late answer for a replaced token or a switched account being shown as current.
- The published package carrying code that is not what this repository builds, or that
  OpenCode would load differently from how it was tested.

### Out of scope

- **A same-user attacker, root, or memory scraping.** OpenCode 2 keeps credentials in
  `opencode.db`, readable by the user, and the plugin runs in OpenCode's process. Anything that
  can read either already has every credential; the plugin adds no storage of its own to
  protect.
- **OpenCode itself.** How OpenCode stores, refreshes and sends credentials is OpenCode's
  boundary. The plugin neither weakens nor strengthens it.
- **Other plugins.** Every plugin in the TUI gets the same context. The plugin cannot hide
  credentials from another plugin, and does not try.
- **A malicious usage endpoint.** The account's credential goes to its own provider, which
  already holds it. What comes back is parsed strictly and shown as numbers only.

## What the plugin does with credentials

- **Reads** them only through `client.credential.list()`, never from `opencode.db`,
  `auth.json` or any file under OpenCode's directories.
- **Projects** each one into a secret-free `AccountView` (id, integration, label, active, kind,
  the OpenAI email from the access token's claims, a static key's last 4 characters). Only that
  reaches UI state. The last 4 characters are shown only for keys of 20 or more characters, so
  at least 16 always stay hidden.
- **Hashes** each value (SHA-256, Web Crypto) to notice a value that changed without an event.
  The digest stays inside the store's private revision tracker.
- **Sends** a value only in a usage request for that same account (see below), read from the
  live entry at request time and kept nowhere after.
- **Writes** only by `client.credential.activate`, after a confirmation. Never
  `credential.create`, `update` or `remove`, never a token refresh, never a token endpoint, and
  never OpenCode's plugin `storage` (enforced by ESLint `no-restricted-syntax`).

No custom cryptography: the digest is a change detector, not a protection.

## Network calls

The only requests the plugin makes:

| Account | Request | Sent |
| --- | --- | --- |
| OpenAI OAuth | `GET https://chatgpt.com/backend-api/wham/usage` | access token as `Bearer`; `ChatGPT-Account-Id` from its claims when present |
| z.ai `zai-coding-plan` key | `GET https://api.z.ai/api/monitor/usage/quota/limit` | API key as `Bearer` |

- **No other host.** Redirects are not followed (`redirect: "manual"`); a redirect reports its
  HTTP status. Every other provider and credential type makes no request.
- **Off switch.** The `usage` plugin option set to false (or any falsy-looking value) sends
  nothing at all.
- **Bounded.** Each request times out after 5 s; `/swap` runs at most 4 at once. After a 401 or
  403 the account is not asked again until its value changes or the user runs `/swap-refresh`.
- **No secret in a failure.** Every failure is fixed text: "request timed out", "network
  error", "HTTP 401", "invalid JSON in response", "unexpected response shape" and similar. An
  exception's own message, a header or a response body never reaches what the plugin shows. A
  header value outside printable ASCII is refused before the request, as "malformed request",
  so a runtime error cannot echo it.

## Diagnostics

The plugin never calls `console.*` (ESLint `no-console`): OpenCode's log is a file other tools
read. The `debug` option only explains why the footer is hidden, with the integration id and a
reason ("env connection", "no stored credential").

## The package

- **Provenance.** Releases are published from GitHub Actions through npm trusted publishing
  (OIDC, no stored token) with provenance; see [Releasing](releasing.md).
- **What is published** is `dist/` (built from `src/` by `scripts/build.ts`), `README.md`,
  `LICENSE` and `package.json`. `check-tui-entry-compiled.sh` refuses a payload that imports
  `effect` or `@opencode/*` at runtime (OpenCode would refuse it), that is not precompiled, or
  that exports anything but `./tui`.
- **Dependencies.** At runtime the plugin uses only modules the host provides; its peers are
  optional, so installing it installs nothing else (`V4/V5`).

## Reporting

See [`SECURITY.md`](../SECURITY.md).
