# AGENTS.md

Guide for AI agents in this repo. Read before change anything.

## What this is

`@leinardi/opencode-swap` = OpenCode 2 TUI plugin, TypeScript on Bun, published to npm. The
repository root is the plugin. OpenCode 2 stores multiple accounts per provider itself
(`credential` table) and switches them natively; the plugin adds only what OpenCode lacks:
footer with the session provider's active account + usage (ChatGPT `wham/usage`, z.ai GLM
Coding Plan quota), `/swap` cross-provider picker, `/swap-next`, `/swap-refresh`. NOT a
credential store. NOT runtime proxy/load balancer (that's `opencode-balancer`). Full picture:
`README.md` + `docs/architecture.md`.

**Maintenance line.** Up to 0.4.x this repo was a Python CLI with its own vault, for OpenCode
1.x, plus an OpenCode 1 TUI plugin. Python CLI retired at 0.4.7 (PyPI frozen). That code lives on
`maint/0.4`, which keeps its own `AGENTS.md`, workflows and docs: on that branch follow those,
not this file. Fixes there are plugin-only, released by hand under npm's `legacy` dist-tag
(`docs/releasing.md#the-04x-line`). Never merge between `main` and `maint/0.4`.

## Before touching anything

Read these two first — most "obvious" changes wrong without context:

1. `docs/opencode-v2-integration.md` — OpenCode 2 behavior the plugin depends on, from source
   (file:line) and sandbox runs: credential API and events, plugin loading/packaging, slots,
   keymap modes, provider→integration mapping, env connections, version minimum. Every
   assumption here re "what OpenCode does" traces back to this file — if OpenCode behavior in
   question, check/update there, don't re-guess. Its "Deviations and open items" lists what is
   still unverified.
2. `docs/architecture.md` — module map, request ownership, revision tracker, cache/back-off
   timing, why `activate` is the only write. Re-deriving from code alone easy to get subtly
   wrong (e.g. 55 s cache vs 60 s poll).

## Core invariants — do not violate

These govern the plugin. OpenCode 2 behavior cited here is in
`docs/opencode-v2-integration.md`; the 0.4.x CLI's own invariants stay with it on `maint/0.4`.

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

- **Never start OpenCode or touch its real dirs from a test.** `bun test` runs with no
  OpenCode, no network, no credentials. Real-OpenCode checks go through
  `scripts/oc2-sandbox.sh` only (isolated `HOME`/`XDG_*`, cleared env, `check` proves real
  dirs untouched) — never the real `opencode` binary, never `~/.local/share|.config|.cache|
  .local/state/opencode*`.
- **Inject fakes, don't patch globals.** `AccountStore` takes every dependency via `StateDeps`
  (client, events, `fetchImpl`, `now`, timers, select/confirm/toast, `sessionStatus`). Tests
  pass fakes: `ManualTimers` in `test/state.test.ts`, `fakeFetch`/`hangingFetch` and friends in
  `test/helpers.ts`, deferred promises for out-of-order answers. `test/plugin.test.ts` runs
  `setup` against a recording context. Reuse these; don't monkeypatch `globalThis.fetch` or
  real timers.
- **Orchestration tests drive the race.** A change to `src/state.ts` needs a test for the
  interleaving it is about: result after switch/update/dispose, event mid-request, deadline,
  revision change with no event. Assert exposed state, not that a mock was called.
- **Fake credentials only.** `makeJwt` (unsigned JWT-shaped tokens, made-up claims) and obvious
  dummy keys. Leak tests plant these and grep exposed state, `JSON.stringify` of it, toasts,
  dialog options and error messages. Anything touching a credential value needs one.
- Verify every change w/ `make check`, plus the matrix row below. If a check needs network,
  real OpenCode or real credentials, test isolation regressed.

## Project conventions

- **Direct pushes to `main` are rejected.** The repo is public with branch
  protection: every change goes through a branch + PR, gated on the required
  CI checks. See `CONTRIBUTING.md`.
- TypeScript, ES modules, Bun 1.3.14. `import type` for types (ESLint
  `consistent-type-imports`); runtime imports only of what the host provides or what ships in
  `dist/`.
- Pure logic in its own module (`accounts`, `session`, `bands`); `usage/*` performs one
  request and parses it. Everything that schedules or owns async work — timers, events, request
  lifetimes, dialogs — lives in `src/state.ts`.
- Comments explain *why*, not *what* — esp citing OpenCode source (file:line), a
  `docs/opencode-v2-integration.md` finding (`V7`, `F5`…) or a specific incident justifying a
  non-obvious choice. Can't cite reason → comment probably shouldn't exist.
- New OpenCode API use = new member in `src/features.ts` `missingFeatures`, checked against the
  2.0.20 types; a newer member raises the minimum version (README, `engines`, `features.ts`,
  integration doc).
- See `docs/architecture.md` for module map before adding new file — most new code belongs in
  existing module.

## Commit messages

All commits MUST be Conventional Commits 1.0.0 **with a scope**:
`<type>(<scope>)[!]: <description>`, optional blank-line body and footers.
Enforced by the `conventional-pre-commit` `commit-msg` hook (`--force-scope`,
installed by `make pre-commit-install`) and by the `conventional-commits` CI
job. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`, `style`, `revert`. Breaking changes use `!` before `:` or a
`BREAKING CHANGE:` footer. Example: `fix(usage): keep the back-off when a
poll returns the same token`.

A release with no explicit version is derived by commitizen (`cz bump
--get-next` in `release.yaml`) from the commits since the last `v<version>`
tag, so a wrong type ships a wrong version:

| Release | Commit | Example |
| --- | --- | --- |
| major | any type with `!` before the colon, or a `BREAKING CHANGE:` footer | `feat(tui-plugin)!: require OpenCode 2.1` |
| minor | `feat` | `feat(usage): add the xai usage lookup` |
| patch | `fix`, `perf`, `refactor` | `fix(footer): keep the label when usage is pending` |
| none | `build`, `chore`, `ci`, `docs`, `revert`, `style`, `test` | `ci(ci): add the ci-result job` |

The highest bump among the commits wins; with only "none" commits since the
last tag, a release with no version fails with "nothing to bump". Bump table
and details: `CONTRIBUTING.md#versioning`.

**Pick the type by whether the change should ship, not by what kind of change
it is.** What ships is the plugin on npm: `src/**`, `scripts/build.ts`, and
`package.json`'s runtime fields (`exports`, `files`, `engines`, peers). A change
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
| `src/**`, `test/**`, `scripts/build.ts`, `package.json`, `bun.lock`, `tsconfig.json`, `eslint.config.ts` | `make plugin-typecheck plugin-lint plugin-test plugin-package-check plugin-entry-check plugin-npm-resolve-check` |
| `scripts/check-npm-resolution.sh`, `scripts/check-tui-entry-compiled.sh` | `make plugin-npm-resolve-check plugin-entry-check`; break what each guards once and see it fail |
| `scripts/check-license-headers.sh`, `test/license-headers.test.ts` | `make plugin-test`; strip a header once and see the check fail |
| `.github/workflows/**` | `pre-commit run actionlint --all-files`; release workflows: `docs/releasing.md` |
| `.pre-commit-config.yaml`, `Makefile`, repo-local `.mk/*.mk` | `make check`, `make help` |
| `*.md`, `.agents/skills/**` | `make check`: markdownlint holds prose to 100 columns; tables, code blocks and headings are exempt |
| a broad change, or merge readiness | `make verify` (what the `verify` CI job runs) |

CI's aggregate check is `ci-result`: it needs `verify`, `pre-commit-all` and
`conventional-commits`.

## Generated or vendored: never hand-edit

- `bun.lock`: regenerate with `bun install` after editing `package.json`.
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
pre-commit hook, tested by `test/license-headers.test.ts`) fails on any that
lacks it. The make-common files above are exempt: they carry make-common's
own header. YAML, JSON, TOML and Markdown are not governed.

## Project skills

Skills live in `.agents/skills/` (symlinked as `.claude/skills`). Load
`adversarial-review` for any review request ("review my diff", "is this ready
to merge").

## Full documentation index

- `README.md` — user-facing overview, install, commands, options, network access, migration.
- `docs/opencode-v2-integration.md` — OpenCode 2 behavior (ground truth), deviations, open items.
- `docs/architecture.md` — module map, data flow, request ownership, packaging.
- `docs/security.md` — threat model, credential handling, network calls.
- `docs/provider-support.md` — what each provider/credential type shows; usage endpoints.
- `docs/testing.md` — test strategy, injected fakes, OpenCode sandbox.
- `docs/releasing.md` — cutting a release (npm), dist-tags, the `maint/0.4` line.
- `CONTRIBUTING.md` — human contributor workflow (PR checklist, testing rules).
- `SECURITY.md` — vulnerability reporting process.
