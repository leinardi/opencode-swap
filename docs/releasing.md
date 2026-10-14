# Releasing

opencode-swap is discontinued. Its last releases are 0.4.7 (the Python CLI on PyPI and the
OpenCode 1 TUI plugin on npm) and 1.0.0 (an npm-only notice for OpenCode 2). This page records
how a release worked, for those two, and the [sunset](#sunset) that follows them.

A release is a `v<version>` tag on `main`, its GitHub release, and the npm publish of that
version.

## Cutting a release

1. Dispatch the **Release** workflow (`.github/workflows/release.yaml`) from `main`. Leave
   `version` empty to derive it from the Conventional Commits since the last `v<version>` tag, or
   pass one (e.g. `1.2.0`, with or without a leading `v`) to force it.
   - A derived version comes from commitizen (`cz bump --get-next`, pinned at 4.19.2 and run
     with `uvx`, configured in `.cz.toml`); see the bump table in
     [`AGENTS.md`](../AGENTS.md#commit-messages). With nothing but "none" commits since
     the last tag, the run fails with "nothing to bump".
   - An explicit version must be higher than the latest release.
   - Dispatched from any other branch, the run fails.
2. Its `bump` job sets the version in `package.json`, regenerates `bun.lock`, pushes a
   `release/v<version>` branch and opens a pull request. It also dispatches `ci.yaml` on that
   branch: a push or pull request made with `GITHUB_TOKEN` does not fire other workflows
   (GitHub's loop prevention), so the required checks would otherwise never appear. A leftover
   `release/v<version>` branch from a failed run is recreated; one with an open pull request
   stops the run.
3. **Review the diff and merge the pull request**, like any other. This is the one manual step,
   deliberately: a human looks at the diff right before anything publishes.
4. The merge is a push to `main`. `.github/workflows/auto-tag-release.yaml` runs on every push to
   `main` but acts only when `package.json`'s version differs from the parent commit's. A commit
   whose parent has no root `package.json` is never a release (that is how the move of the
   plugin to the repository root stayed one). On a version change it:
   - creates the `v<version>` tag at that commit (the immutable-tags ruleset blocks moving or
     deleting a tag, not creating one, so `GITHUB_TOKEN` with `contents: write` is enough);
   - creates the GitHub release with generated notes, taking GitHub's **Latest** badge only when
     it is the highest `v<version>`;
   - dispatches `publish-tui-plugin.yml` against the tag while npm lacks the version (a tag
     pushed with `GITHUB_TOKEN` would not fire its `push: tags` trigger; dispatching against the
     tag ref gives it the same `ref` and `ref_type` a real tag push would).

   Every stage checks what an earlier attempt left behind, so **re-running a failed
   `auto-tag-release.yaml` run finishes the release**: it creates only a missing tag and release,
   fails if the tag already marks another commit, and dispatches the publisher only while npm
   still lacks the version. Wait for a dispatched publisher to finish before re-running, or it
   runs twice.
5. `publish-tui-plugin.yml` checks that the tag is `v` plus `package.json`'s version, installs
   the locked dependencies, builds, runs the unit tests, typecheck, lint and payload checks,
   resolves the packed peers with OpenCode's Arborist and checks the compiled entry. Then it
   publishes with npm trusted publishing (OIDC, no stored token) and provenance, under the
   dist-tag below, and creates the GitHub release if it is still missing.

Net effect: dispatch once, review one pull request, merge once. Tags are immutable once pushed,
so get the version right before merging the bump pull request.

`workflow_dispatch` remains available on `publish-tui-plugin.yml` for a manual re-run; it
refuses to publish a version npm already has.

## npm dist-tags

| Dist-tag | Holds | Installed by |
| --- | --- | --- |
| `latest` | the 1.x discontinuation notice (OpenCode 2) | `opencode plugin add @leinardi/opencode-swap`, and any bare package name |
| `legacy` | the newest 0.4.x release (OpenCode 1) | `@leinardi/opencode-swap@legacy` |

npm moves `latest` to whatever is published without `--tag`. The publisher therefore compares
the version with the current `latest` and publishes anything lower under `legacy`, so a 0.4.x
fix never takes `latest` back from 1.x. OpenCode 1 users pin `@leinardi/opencode-swap@0.4`, a
semver range that resolves to the newest 0.4.x whatever the dist-tags say.

0.4.7, released before 1.0.0, was published as `latest` and is moved by 1.0.0. Once 1.0.0 is
out, point `legacy` at it by hand, once:

```bash
npm dist-tag add @leinardi/opencode-swap@0.4.7 legacy
```

## Sunset

In order, once 0.4.7 and then 1.0.0 are published:

1. Point `legacy` at the last OpenCode 1 release:

   ```bash
   npm dist-tag add @leinardi/opencode-swap@0.4.7 legacy
   ```

2. Mark every version deprecated on npm. `npm install` then prints the message; OpenCode installs
   the package as before.

   ```bash
   npm deprecate @leinardi/opencode-swap "opencode-swap is discontinued: see https://github.com/leinardi/opencode-swap#readme"
   ```

3. Archive the `opencode-swap` project on PyPI (project settings, "Archive project"). Installs
   keep working; the page says the project is no longer maintained.
4. Push the `opencode2-plugin-final` tag, if it is not on GitHub yet.
5. Archive the GitHub repository (Settings, "Archive this repository"), last: an archived
   repository runs no workflows, so every release above must be finished first.

## One-time setup (already done)

Kept as a reference for what makes the above possible, not something to repeat per release.

**npm.** npm attaches a trusted publisher only to a package that exists, so the package was
first published by hand from the personal npm account `leinardi` (`@leinardi` is that
account's scope). Then, on `https://www.npmjs.com/package/@leinardi/opencode-swap/access`, a
trusted publisher was added: GitHub Actions, owner `leinardi`, repository `opencode-swap`,
workflow `publish-tui-plugin.yml`, environment `npm`, with two-factor authentication required
and bypass tokens disallowed. The workflow's file name and environment must not change.

**Repository settings.** "Allow GitHub Actions to create and approve pull requests" is enabled
(Settings → Actions → General → Workflow permissions), which `release.yaml` needs to open its
pull request. Merging still requires the same review as any other pull request.

Never replace trusted publishing with a stored `NPM_TOKEN`.
