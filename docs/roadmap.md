# Roadmap

## What v1 does

- Secure per-account storage: macOS Keychain, or atomic `0600` files under a
  `0700` directory on Linux.
- Imports the currently-active OpenCode account, with identity dedup and
  refresh-in-place on re-import.
- Transactional switching: sync-back of rotated tokens plus rollback on
  failure, covered by a full failure-injection test matrix.
- Multi-provider, provider-scoped accounts, including generic API providers
  and selected safe OAuth handlers.
- Full CLI: `add`, `list`, `current`, `status`, `use`, `switch`, `remove`,
  `rename`, `export`, `import`, `restore`, `doctor`.
- Password-encrypted export/import of all accounts, with conflict checks
  before anything is written.
- Backup and recovery through `restore` (including `--pristine` for the very
  first snapshot ever taken) and `doctor` diagnostics for paths, schema, and
  secret backend.
- An optional OpenCode TUI plugin showing active account and usage in the
  session prompt, with account switching from the command palette.
- Live usage lookup (`list --usage` / `status --usage`) for OpenAI ChatGPT
  OAuth and the Z.AI GLM Coding Plan (`zai-coding-plan`), with window lengths
  derived from the provider's response rather than hardcoded.
- A test suite spanning unit, integration, failure injection, real
  multi-thread concurrency, compatibility, and security categories; see
  [`docs/testing.md`](testing.md).

## Known gaps

- **Standalone OAuth refresh is limited to OpenAI, and only reachable through
  `list --usage`/`status --usage`/the explicit `refresh` command.**
  `oauth_refresh.py` couples to OpenCode's private `client_id`
  (`docs/opencode-auth.md#loading-and-refresh`) by design, reusing the same
  public PKCE grant OpenCode itself would make on its next request. It exists
  because `fetch_usage`/`account_validity` need to tell "this account's token
  is genuinely dead" from "this account just isn't the one OpenCode has live
  right now" — the latter is resolved for free by preferring the live
  `auth.json` record over the stored snapshot (see
  `Switcher._live_attribution`), never by a network call. No other command
  triggers a refresh, and the account currently active in OpenCode is never
  refreshed this way — OpenCode owns that refresh on its own next request.
- **No Windows support.** `locking.py` is POSIX-only (`fcntl`), and the
  storage backend comparison in [`docs/security.md`](security.md) only
  covers Linux and macOS. Windows support will only happen if volunteers
  step up to implement and maintain it.
- **Non-OpenAI live verification wanted.** OpenAI is the only provider
  tested end to end with real accounts; everything else is source-derived
  and covered by synthetic tests only. See
  [`docs/provider-support.md`](provider-support.md).

  > 🧪 **Testers wanted.** If you use a non-OpenAI provider, please open an
  > issue with the provider, auth method, OpenCode version, and sanitized
  > failure details. **Never include credentials.** Use
  > [`docs/provider-research-prompt.md`](provider-research-prompt.md) for a
  > copy-paste OpenCode prompt that gathers safe source evidence for one
  > provider.
