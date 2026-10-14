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

These govern the OpenCode 2 TUI plugin (`integrations/opencode-tui-plugin`). The Python CLI is
frozen at 0.4.x and leaves the repo; until then its own rules stay in `docs/architecture.md` and
`docs/security.md`. OpenCode 2 behavior cited here is in `docs/opencode-v2-integration.md`.

- **No secrets in UI, logs, errors or storage.** Credential values (access and refresh tokens,
  API keys) never reach exposed state, toasts, dialog text, the footer, thrown messages or any
  log. Only `AccountView` (`src/accounts.ts`) reaches UI state; a value goes from a fresh
  `credential.list()` straight to the one usage request that needs it (`usageRequest`), and the
  revision tracker keeps a digest, never the value. Usage failures are fixed text, never an
  exception message, header or response body (`src/usage/http.ts`). The one shown fragment is a
  static key's last 4 characters (`…abcd`, keys of 20+ characters only). No `console.*`
  anywhere (ESLint `no-console`). Tests plant token values and grep for them.
- **Only `credential.activate`.** The plugin switches which stored credential is active, and
  nothing else: never `credential.create`, `update` or `remove`, never a token refresh, never a
  token endpoint, never `storage.store`/`storage.memory` (ESLint `no-restricted-syntax`). It
  never changes the session's model.
- **No OpenCode DB or file access.** Everything goes through the plugin context (`client`,
  `data`, `ui`). Never open `opencode.db`, `auth.json` or anything under OpenCode's data dirs.
- **Network only to the two usage endpoints, with that account's own credential.**
  `chatgpt.com/backend-api/wham/usage` for OpenAI OAuth, `api.z.ai/.../quota/limit` for z.ai
  keys; redirects are not followed. The `usage` option set to false sends nothing at all.
- **Type-only `effect` and `@opencode/*`.** OpenCode 2 refuses a plugin-local copy of its
  runtime packages; import their types only (ESLint `no-restricted-imports` with
  `allowTypeImports`, and `scripts/check-tui-entry-compiled.sh` on the packed payload).
- **`./tui`-only, precompiled output.** `exports` is exactly `{"./tui": "./dist/tui.js"}` (a `"."`
  export makes OpenCode register a server plugin), `files` is `dist/`, and every file is
  Solid's universal-renderer output from `scripts/build.ts`; raw JSX from npm renders without
  reactivity. `check-tui-entry-compiled.sh` and `check-npm-resolution.sh` guard the payload.
- **Unknown shapes are never coerced.** A credential value short of OpenCode's schema is
  `kind: "unknown"`: listed and switchable by label, with no email and no usage (F7). A response
  of an unexpected shape is "unexpected response shape", not a guess. A context missing any
  member the plugin calls registers nothing and shows one toast naming the minimum OpenCode
  (`src/features.ts`).
- **Late async results are never applied.** Every usage request is owned by its credential's
  generation and `credentialID#revision` key, and aborted when a switch or update event, a
  revision change or dispose supersedes it (`src/state.ts`). Nothing runs after dispose. The
  displayed active account changes only from a fresh list or the `credential.switched` event,
  never optimistically.

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

- **Direct pushes to `main` are rejected.** The repo is public with branch
  protection: every change goes through a branch + PR, gated on the required
  CI checks. See `CONTRIBUTING.md`.
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

## Commit messages

All commits MUST be Conventional Commits 1.0.0 **with a scope**:
`<type>(<scope>)[!]: <description>`, optional blank-line body and footers.
Enforced by the `conventional-pre-commit` `commit-msg` hook (`--force-scope`,
installed by `make pre-commit-install`) and by the `conventional-commits` CI
job. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`, `style`, `revert`. Breaking changes use `!` before `:` or a
`BREAKING CHANGE:` footer. Example: `fix(auth): keep the rotated refresh
token on a self-switch`.

A release with no explicit version is derived by commitizen (`cz bump
--get-next` in `release.yaml`) from the commits since the last `v<version>`
tag, so a wrong type ships a wrong version:

| Release | Commit | Example |
| --- | --- | --- |
| major | any type with `!` before the colon, or a `BREAKING CHANGE:` footer (also from 0.x) | `feat(cli)!: rename the export command` |
| minor | `feat` | `feat(providers): add the xai OAuth record` |
| patch | `fix`, `perf`, `refactor` | `fix(auth): keep the rotated refresh token` |
| none | `build`, `chore`, `ci`, `docs`, `revert`, `style`, `test` | `ci(ci): add the ci-result job` |

The highest bump among the commits wins; with only "none" commits since the
last tag, a release with no version fails with "nothing to bump". Bump table
and details: `CONTRIBUTING.md#versioning`.

**Pick the type by whether the change should ship, not by what kind of change
it is.** What ships is the TUI plugin on npm. A change
users should receive is `fix` (or `feat`). `perf` and `refactor` bump a patch
under commitizen, so never use them for a change that ships nothing (CI,
docs, tests, tooling): type that `ci`, `docs`, `test`, `build` or `chore`. A
`revert` bumps nothing, so a revert of a shipped `feat` or `fix` is itself a
`fix`. A comment-only change to a shipped file (a licence header, a typo) may
be `docs` or `chore`.

commitizen reads **every line** of every commit message, merge commits
included, and counts a line that starts with a word, an optional scope and a
colon when the word begins with `feat`, `fix`, `refactor` or `perf`. So a body
line such as `fixtures: ...` or `performance: ...` bumps a patch, and so does
a pull request title typed `fix(...)`: GitHub writes the title into the merge
commit's body. PRs land as merge commits, so every commit counts, and so does
the PR title.

## Validation matrix

Run before considering an edit done. `make check` applies to every row.

| You edited | Run |
| --- | --- |
| `integrations/opencode-tui-plugin/**` | `make tui-plugin-typecheck tui-plugin-lint tui-plugin-test tui-plugin-package-check tui-plugin-entry-check tui-plugin-npm-resolve-check` |
| `scripts/check-npm-resolution.sh`, `scripts/check-tui-entry-compiled.sh` | `make tui-plugin-npm-resolve-check tui-plugin-entry-check`; break what each guards once and see it fail |
| `scripts/check-license-headers.sh`, `integrations/opencode-tui-plugin/test/license-headers.test.ts` | `make tui-plugin-test`; strip a header once and see the check fail |
| `.github/workflows/**` | `pre-commit run actionlint --all-files`; release workflows: `docs/releasing.md` |
| `.pre-commit-config.yaml`, `Makefile`, repo-local `.mk/*.mk` | `make check`, `make help` |
| `*.md`, `.agents/skills/**` | `make check`: markdownlint holds prose to 100 columns; tables, code blocks and headings are exempt |
| a broad change, or merge readiness | `make verify` (what the `verify` CI job runs) |

CI's aggregate check is `ci-result`: it needs `verify`, `pre-commit-all` and
`conventional-commits`.

## Generated or vendored: never hand-edit

- `integrations/opencode-tui-plugin/bun.lock`: regenerate with `bun install`
  in the plugin directory after editing its `package.json`.
- `.mk/help.mk`, `.mk/password.mk`, `.mk/pre-commit.mk`,
  `.mk/.mk-common-version` and `scripts/bootstrap-mk-common.sh`: fetched from
  `leinardi/make-common` (the Makefile's `MK_COMMON_FILES`); refresh with
  `make mk-common-update`, and port a change upstream instead.

## License headers

Every tracked shell, TypeScript and JavaScript source, the `Makefile`
and the repo-local `.mk` fragments carry the MIT header, with the year of the
file's first commit; a shebang stays on the first line. Copy it from an
existing file of the same kind, or use the IntelliJ copyright profile in
`.idea/copyright/`. `scripts/check-license-headers.sh` (an always-run
pre-commit hook, tested by the TUI plugin's `test/license-headers.test.ts`) fails on any that
lacks it. The make-common files above are exempt: they carry make-common's
own header. YAML, JSON, TOML and Markdown are not governed.

## Project skills

Skills live in `.agents/skills/` (symlinked as `.claude/skills`). Load
`adversarial-review` for any review request ("review my diff", "is this ready
to merge").

## Full documentation index

- `README.md` — user-facing overview, install, commands, quickstart.
- `docs/architecture.md` — module map, switch algorithm, transaction design.
- `docs/opencode-auth.md` — OpenCode's `auth.json` format/lifecycle (ground truth).
- `docs/security.md` — threat model, storage backend comparison.
- `docs/testing.md` — testing strategy in depth.
- `docs/roadmap.md` — milestone status (what's built, what's postponed).
- `docs/releasing.md` — how to cut a release (CLI on PyPI and TUI plugin on npm, in lockstep).
- `CONTRIBUTING.md` — human contributor workflow (PR checklist, testing rules).
- `SECURITY.md` — vulnerability reporting process.
