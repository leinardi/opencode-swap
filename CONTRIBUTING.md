# Contributing

Thanks for considering a contribution to `opencode-swap`. This is a small, solo-maintained
project that runs next to other people's credentials: read this and [`AGENTS.md`](AGENTS.md)
before sending a change. Both apply equally to human and AI-assisted contributions.

## Getting set up

You need [Bun](https://bun.sh) 1.3.14, Node.js with npm (the payload checks pack the plugin and
resolve it the way OpenCode does), and Python for the pre-commit hooks.

```bash
make plugin-sync      # install locked dependencies
make plugin-test      # unit tests: no network, no OpenCode, no credentials
make verify           # what CI's verify job runs
```

`make help` lists every target. `make check` runs the pre-commit hooks (ESLint, actionlint,
markdownlint, shellcheck, the licence-header check, …) on every file.

Never run a change against your own OpenCode installation or accounts. Use the isolated
sandbox and fake credentials described in [`docs/testing.md`](docs/testing.md#the-opencode-sandbox).

## Before you open a PR

Run `make check` locally *before creating your commit*, and `make verify` before asking for a
merge: they are what CI runs. The [PR template](.github/PULL_REQUEST_TEMPLATE.md) checklist
expects this, and the validation matrix in [`AGENTS.md`](AGENTS.md#validation-matrix) lists what
each kind of change additionally owes.

This repository requires all changes to land through a pull request against `main` (branch
protection is enabled) with the required CI checks passing. A fix for the OpenCode 1 plugin
goes against `maint/0.4` instead; see [`docs/releasing.md`](docs/releasing.md#the-04x-line).

Install the git hooks once with `make pre-commit-install`. It installs both the `pre-commit` and
the `commit-msg` hooks, so commit messages are checked when you commit, not when the release
runs.

## Commit messages

All commits must follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
with a scope: `<type>(<scope>)[!]: <description>`. The `conventional-pre-commit` hook enforces
this on `commit-msg`, and the `conventional-commits` CI job checks it again on every pull
request. Release notes are not built from these messages: `gh release create --generate-notes`
lists the merged pull requests by title.

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`,
`style`, `revert`. Use a lower-case, imperative description:

```text
fix(usage): keep the back-off when a poll returns the same token
feat(tui-plugin): show the plan name when a provider reports no windows
feat(tui-plugin)!: require OpenCode 2.1
```

Mark breaking changes with `!` before the colon or a `BREAKING CHANGE: <description>` footer.

## Versioning

A release with no explicit version is derived from these types by commitizen, from the commits
since the last `v<version>` tag:

| Release | Commit |
| --- | --- |
| major | any type with `!` before the colon, or a `BREAKING CHANGE:` footer |
| minor | `feat` |
| patch | `fix`, `perf`, `refactor` |
| none | `build`, `chore`, `ci`, `docs`, `revert`, `style`, `test` |

The highest bump among the commits wins. commitizen counts `perf` and `refactor` as a patch, so
either one is enough to make a release on its own. Type a change by whether npm users should
receive it: `fix` or `feat` for what changes the published plugin, and `build`, `chore`, `ci`,
`docs` or `test` for what does not. A `revert` of a shipped `feat` or `fix` bumps nothing; type
it `fix` when the revert should ship.

Pull requests are merged with merge commits, so every commit in a pull request lands on `main` as
it is and counts toward the version: each commit needs a correct type, not just the pull request
as a whole.

commitizen reads every line of every commit message, merge commits included, and bumps on a line
that starts with a word beginning with `feat`, `fix`, `refactor` or `perf`, an optional scope
and a colon. A body line such as `fixtures: ...` therefore bumps a patch, and so does a pull
request title typed `fix(...)`, because GitHub writes the title into the merge commit's body.

## Invariants a PR must not violate

These are load-bearing; [`AGENTS.md`](AGENTS.md#core-invariants--do-not-violate) has the full
list and the reasoning behind each:

- **No secrets in UI, logs, errors or storage.** Only the secret-free `AccountView` reaches UI
  state; usage failures are fixed text; no `console.*`.
- **Only `credential.activate`.** Never create, update or remove a credential, never refresh a
  token, never call a token endpoint, never change the session's model.
- **No OpenCode DB or file access.** Everything goes through the plugin context.
- **Network only to the two usage endpoints, with that account's own credential**, and
  nothing at all with `usage` off.
- **Type-only `effect` and `@opencode/*`.**
- **`./tui`-only, precompiled output.**
- **Unknown shapes are never coerced.**
- **Late async results are never applied.**

## Testing rules

- Tests use injected fakes for every dependency of `AccountStore`: the credential client,
  events, `fetchImpl`, the clock, timers, dialogs and toasts. Never a real OpenCode, network or
  credential.
- A change to orchestration (`src/state.ts`) needs an orchestration test that drives the race
  it is about: a late result, an event, a deadline, dispose.
- A change that touches a credential value needs a leak test: plant a fake token and assert it
  appears nowhere it should not.

See [`docs/testing.md`](docs/testing.md) for the full testing strategy.

## Provider support

The plugin switches every provider's credentials; what is provider-specific is the email, the
key hint and the usage lookup. Adding a usage lookup needs the endpoint, its authentication and
a documented or observed response shape, with fixed-text failures and leak tests; see
[`docs/provider-support.md`](docs/provider-support.md). A report of how a provider's usage
behaves with a real account is welcome as an issue, **without credentials**.

## Project conventions

TypeScript, ES modules, Solid for UI. Pure functions in their own modules; `src/usage/*`
performs one request; everything that schedules or owns async work (timers, events, request
lifetimes, dialogs) in `src/state.ts`. Comments explain *why* (citing OpenCode source behaviour, a
sandbox finding or a specific incident), not *what*. See
[`docs/architecture.md`](docs/architecture.md) for the module map before adding a new file: most
changes belong in an existing module.

## Releasing

Maintainer-only; see [`docs/releasing.md`](docs/releasing.md).
