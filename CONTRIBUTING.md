# Contributing

Thanks for considering a contribution to `opencode-swap`. This is a small,
solo-maintained project handling other people's credentials — read this and
[`AGENTS.md`](AGENTS.md) before sending a change, both apply equally to human
and AI-assisted contributions.

## Getting set up

```bash
uv sync --dev
make python-test          # No network/keychain access required
```

`make help` lists all targets. Root `make verify` (what CI runs) additionally
needs [Bun](https://bun.sh) 1.3.14 for the TUI plugin's typecheck, lint, and
npm payload check:

```bash
make verify
```

`make check` (pre-commit hooks: ruff, mypy, actionlint, markdownlint, …) is
Python/YAML/Markdown-only and needs no Bun, network, or keychain access.

## Before you open a PR

Run `make check` locally *before creating your commit* — it's the same set
of checks CI runs. The [PR template](.github/PULL_REQUEST_TEMPLATE.md)
checklist expects this.

This repository requires all changes to land through a pull request against
`main` (branch protection is enabled) with the required CI checks passing.

Install the git hooks once with `make pre-commit-install`. It installs both
the `pre-commit` and the `commit-msg` hooks, so commit messages are checked
when you commit, not when the release runs.

## Commit messages

All commits must follow
[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
with a scope: `<type>(<scope>)[!]: <description>`. The
`conventional-pre-commit` hook enforces this on `commit-msg`, and the
`conventional-commits` CI job checks it again on every pull request. Release
notes are not built from these messages: `gh release create --generate-notes`
lists the merged pull requests by title.

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`,
`ci`, `chore`, `style`, `revert`. Use a lower-case, imperative description:

```text
fix(auth): keep the rotated refresh token on a self-switch
feat(cli): add an export command
feat(store)!: move the registry to a new location
```

Mark breaking changes with `!` before the colon or a
`BREAKING CHANGE: <description>` footer.

## Versioning

A release with no explicit version is derived from these types by
commitizen, from the commits since the last `v<version>` tag:

| Release | Commit |
| --- | --- |
| major | any type with `!` before the colon, or a `BREAKING CHANGE:` footer |
| minor | `feat` |
| patch | `fix`, `perf`, `refactor` |
| none | `build`, `chore`, `ci`, `docs`, `revert`, `style`, `test` |

The highest bump among the commits wins. commitizen counts `perf` and
`refactor` as a patch, so either one is enough to make a release on its own.
A `revert` of a shipped `feat` or `fix` bumps nothing; type it `fix` when the
revert should ship.

Pull requests are merged with merge commits, so every commit in a pull
request lands on `main` as it is and counts toward the version: each commit
needs a correct type, not just the pull request as a whole.

## Invariants a PR must not violate

These are load-bearing; see [`AGENTS.md`](AGENTS.md) for the full list and
the reasoning/incidents behind each one:

- **`auth.json` writes are always atomic** (temp file, `chmod 0600`,
  `os.replace`) — see `atomic.py`.
- **Never silently guess an unrecognized credential schema.** Raise
  `SchemaError` and refuse rather than coerce or drop fields.
- **Never lose a live credential without preserving it first** — `use_account`
  must sync back rotated tokens or stash an unmanaged login before
  overwriting the active provider entry.
- **Secrets never touch the non-secret registry** (`registry.json` /
  `models.AccountMeta`). Only `SecretStore` (macOS Keychain or private file)
  holds token/key material. The one bounded exception — a static-key
  account's last-4-characters hint — is documented in `AGENTS.md`.
- **No secrets in CLI output, logs, or error messages.** Account ids and keys
  are truncated to their last four characters; tests grep stdout/stderr for
  planted secret values.
- **No custom cryptography.** The storage model is macOS Keychain first, with
  `chmod 0600` files as fallback and the only Linux backend — this matches
  (not exceeds) OpenCode's own trust boundary by design; see
  [`docs/security.md`](docs/security.md).
- **Every mutating `Switcher` operation holds `self.lock`.**

## Testing rules

- Never let a test touch the real macOS Keychain — construct
  `Switcher`/`SecretStore` with `platform=Platform.UNKNOWN`, or monkeypatch
  `macos_keychain.*` with an in-memory fake. `tests/test_cli.py`'s
  `isolated_env` autouse fixture already does this for CLI tests.
- Never let a test touch the real `~/.local/share/opencode/auth.json` — point
  `Switcher(opencode_auth_path=...)` at `tmp_path`, or set `XDG_DATA_HOME` via
  `monkeypatch.setenv` for CLI tests.
- Use lowercase account names in tests (`normalize_account_name` lowercases
  them; mismatched-case lookups have caused real bugs).
- Failure-injection tests should target one specific write call, not a broad
  monkeypatch like `json.dumps` — see `test_switcher_use.py` for the pattern.

See [`docs/testing.md`](docs/testing.md) for the full testing strategy.

## Provider support

Adding or verifying a provider needs source-backed evidence, not assumption:
static API providers use the existing generic handling, and OAuth requires
evidence for identity, refresh, and expiry semantics (see `AGENTS.md` and
`docs/provider-support.md`). If you use a non-OpenAI provider,
[`docs/provider-research-prompt.md`](docs/provider-research-prompt.md) has a
copy-paste OpenCode prompt that gathers exactly that evidence safely, and the
"Provider research / tester report" issue template is where it belongs.

## Project conventions

Python 3.12+, stdlib `argparse` (no Click/Typer), dataclasses over ad-hoc
dicts, `from __future__ import annotations` everywhere, comments that explain
*why* (citing OpenCode source behavior or a specific incident) rather than
*what*. See [`docs/architecture.md`](docs/architecture.md) for the module map
before adding a new file — most changes belong in an existing module.

## Releasing

Maintainer-only; see [`docs/releasing.md`](docs/releasing.md).
