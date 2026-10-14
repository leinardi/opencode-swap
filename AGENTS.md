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
  Stop. One bounded exception: for a static-key account (which has no
  account id) the `account_id` slot holds the API key's **last 4 characters**
  as a "which key is this" hint — see `providers.common.key_account_hint`,
  matching what providers show on their own key dashboards. 4 trailing chars
  of a high-entropy key is not the credential; anything longer or
  reconstructible is.
- **No secrets in CLI output, logs, error messages.** `cli.py` truncates
  account ids to last 4 chars (and shows only a key's last 4 for static-key
  accounts, per the exception above), never prints token fields. Anything
  added to CLI output needs same discipline — tests grep stdout/stderr for
  planted secret values.
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
it is.** What ships is the CLI on PyPI and the TUI plugin on npm. A change
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
| `src/**/*.py`, `tests/**` | `uv run pytest -q` |
| `pyproject.toml`, `uv.lock` | `uv run pytest -q`, then `make python-build` |
| `integrations/opencode-tui-plugin/**` | `make tui-plugin-typecheck tui-plugin-lint tui-plugin-package-check tui-plugin-entry-check tui-plugin-npm-resolve-check` |
| `scripts/check-npm-resolution.sh`, `scripts/check-tui-entry-compiled.sh` | `make tui-plugin-npm-resolve-check tui-plugin-entry-check`; break what each guards once and see it fail |
| `scripts/check-license-headers.sh`, `tests/test_license_headers.py` | `uv run pytest -q tests/test_license_headers.py`; strip a header once and see the check fail |
| `.github/workflows/**` | `pre-commit run actionlint --all-files`; release workflows: `docs/releasing.md` |
| `.pre-commit-config.yaml`, `Makefile`, repo-local `.mk/*.mk` | `make check`, `make help` |
| `*.md`, `.agents/skills/**` | `make check`: markdownlint holds prose to 100 columns; tables, code blocks and headings are exempt |
| a broad change, or merge readiness | `make verify` (what the `verify` CI job runs) |

CI's aggregate check is `ci-result`: it needs `verify`, `pre-commit-all` and
`conventional-commits`.

## Generated or vendored: never hand-edit

- `uv.lock`: regenerate with `uv lock` after editing `pyproject.toml`.
- `integrations/opencode-tui-plugin/bun.lock`: regenerate with `bun install`
  in the plugin directory after editing its `package.json`.
- `.mk/help.mk`, `.mk/password.mk`, `.mk/pre-commit.mk`,
  `.mk/.mk-common-version` and `scripts/bootstrap-mk-common.sh`: fetched from
  `leinardi/make-common` (the Makefile's `MK_COMMON_FILES`); refresh with
  `make mk-common-update`, and port a change upstream instead.

## License headers

Every tracked Python, shell, TypeScript and JavaScript source, the `Makefile`
and the repo-local `.mk` fragments carry the MIT header, with the year of the
file's first commit; a shebang stays on the first line. Copy it from an
existing file of the same kind, or use the IntelliJ copyright profile in
`.idea/copyright/`. `scripts/check-license-headers.sh` (an always-run
pre-commit hook, tested by `tests/test_license_headers.py`) fails on any that
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
