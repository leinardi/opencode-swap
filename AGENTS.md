# AGENTS.md

Guide for AI agents in this repo. Read before change anything.

## What this is

opencode-swap = **discontinued**. Repo about to be archived. `main` ships only
`@leinardi/opencode-swap` 1.x on npm: an OpenCode 2 TUI plugin (`src/tui.ts`) that shows one
warning asking users to remove it, nothing else. History holds the real code: tag `v0.4.7` (Python
CLI + OpenCode 1 TUI plugin), tag `opencode2-plugin-final` (unreleased OpenCode 2 plugin). Don't
revive either here; a fork is the place.

## Invariants — do not violate

- **Plugin id stays `opencode-swap`.** OpenCode 2 turns OpenCode 1's
  `"plugin_enabled": {"opencode-swap": true}` into a bare `"opencode-swap"` plugins entry. A bare
  entry only enables a loaded plugin with that id; otherwise OpenCode installs it from npm, where
  `opencode-swap` is an unrelated package. Evidence: `docs/opencode-v2-integration.md`, "On a real
  installation". `test/tui.test.ts` asserts the id.
- **The notice touches nothing but `ui.toast`.** No credential, client, network, storage or file
  access. `test/tui.test.ts` fails on any other context read.
- **`./tui`-only, precompiled payload**, no runtime `effect`/`@opencode/*` import:
  `scripts/check-tui-entry-compiled.sh` and `scripts/check-npm-resolution.sh` guard the packed
  tarball.

Sunset steps after the last release: `docs/releasing.md#sunset`.

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
last tag, a release with no version fails with "nothing to bump".

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
| `*.md` | `make check`: markdownlint holds prose to 100 columns; tables, code blocks and headings are exempt |
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
