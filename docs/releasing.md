# Releasing

This repository ships one package, `@leinardi/opencode-swap` on npm. A release is a `v<version>`
tag on `main`, its GitHub release, and the npm publish of that version.

The Python CLI (`opencode-swap` on PyPI) is retired at 0.4.7 and gets no further releases. The
0.4.x TUI plugin for OpenCode 1 lives on the `maint/0.4` branch; see
[The 0.4.x line](#the-04x-line).

## Cutting a release

1. Dispatch the **Release** workflow (`.github/workflows/release.yaml`) from `main`. Leave
   `version` empty to derive it from the Conventional Commits since the last `v<version>` tag, or
   pass one (e.g. `1.2.0`, with or without a leading `v`) to force it.
   - A derived version comes from commitizen (`cz bump --get-next`, pinned at 4.19.2 and run
     with `uvx`, configured in `.cz.toml`); see the bump table in
     [`CONTRIBUTING.md`](../CONTRIBUTING.md#versioning). With nothing but "none" commits since
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
| `latest` | the newest 1.x release (OpenCode 2) | `opencode plugin add @leinardi/opencode-swap`, and any bare package name |
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

## The 0.4.x line

`maint/0.4` is cut from `v0.4.7` and carries the Python CLI and the OpenCode 1 TUI plugin as
they were, with their own workflows, `AGENTS.md` and documentation. Its workflows release only
from `main`, so a fix there is released by hand. Only the plugin is released: PyPI stays at
0.4.7.

1. Branch from `maint/0.4` and open the fix as a pull request against `maint/0.4`; the branch
   ruleset covers `maint/*`, so it needs the same checks and review as `main`.
2. In a second pull request against `maint/0.4`, set the plugin's version,
   `integrations/opencode-tui-plugin/package.json`, to the next `0.4.N`, and regenerate its
   lockfile with `bun install --cwd integrations/opencode-tui-plugin`. Leave `pyproject.toml`
   alone.
3. After merging it, tag the merge commit `tui-v0.4.N` and push the tag, then create its
   GitHub release without the **Latest** badge:

   ```bash
   git tag tui-v0.4.N <merge-commit>
   git push origin tui-v0.4.N
   gh release create tui-v0.4.N --verify-tag --generate-notes --latest=false
   ```

   The pushed tag fires `maint/0.4`'s own `publish-tui-plugin.yml` (`tui-v*`), which publishes
   0.4.N under `legacy` because it is lower than `latest`. Create the release right away: the
   publisher creates it itself if it is still missing when it finishes, and GitHub may then
   give it the **Latest** badge; `gh release edit v<newest 1.x> --latest` puts it back.
4. Check `npm view @leinardi/opencode-swap dist-tags`: `latest` unchanged, `legacy` at 0.4.N.

**Never push a `v*` tag on `maint/0.4`**: there it fires `publish-pypi.yml`. Never merge
`maint/0.4` into `main` or the other way round.

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
