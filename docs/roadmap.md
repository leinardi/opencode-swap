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
- A test suite spanning unit, integration, failure injection, real
  multi-thread concurrency, compatibility, and security categories; see
  [`docs/testing.md`](testing.md).

## Known gaps

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
