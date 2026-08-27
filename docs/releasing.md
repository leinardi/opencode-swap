# Releasing

This project ships two packages from one repo — the Python CLI (PyPI) and
the optional npm TUI plugin — bumped and released **in lockstep**, one
version for both.

## Cutting a release

1. Dispatch the **Release** workflow (`.github/workflows/release.yaml`) with
   the new version (e.g. `0.4.0`, with or without a leading `v`).
2. Its `bump` job checks out `main`, bumps `pyproject.toml`,
   `src/opencode_swap/__init__.py`, and
   `integrations/opencode-tui-plugin/package.json` to that version,
   regenerates `uv.lock` and `bun.lock`, pushes a `release/v<version>`
   branch, and opens a PR. It also explicitly dispatches `ci.yml` and
   `pr-lint.yml` against that branch — a bot-authored push/PR does not fire
   other workflows' `push`/`pull_request` triggers on its own (GitHub's
   loop-prevention), so without this the PR's required `verify` check would
   never appear.
3. **Review the diff and merge the PR** the same way as any other PR (the
   existing admin bypass-merge, since self-approval is impossible under
   `CODEOWNERS` + required review). This is the one manual step in the whole
   flow, and deliberately so — a human looks at the diff right before
   anything publishes.
4. That merge is an ordinary push to `main`. `.github/workflows/auto-tag-release.yml`
   runs on every push to `main`, but only acts on the one that actually
   changed `pyproject.toml`'s version (every other push — a bugfix, a
   Dependabot merge, this very PR's own merge before any version bump has
   ever happened — no-ops instantly). When it detects a version change, it:
   - creates tags `v<version>` and `tui-v<version>` at that commit (tag
     creation isn't covered by the branch-protection ruleset — only
     `refs/heads/*` is — so this needs no bypass, no PAT, just
     `GITHUB_TOKEN` with `contents: write`)
   - creates both GitHub releases with generated notes
   - explicitly dispatches `publish-pypi.yml` and `publish-tui-plugin.yml`
     against their respective tags (same loop-prevention workaround as
     step 2 — a `GITHUB_TOKEN`-pushed tag wouldn't fire their `push: tags:`
     triggers on its own; dispatching `workflow_dispatch` against the tag
     ref gives them the same `ref`/`ref_type` context a real tag push would)
5. Both publish workflows run independently: `publish-pypi.yml` re-verifies
   the tag matches `pyproject.toml`, runs the test suite, builds the wheel
   and sdist, publishes to PyPI via OIDC trusted publishing (no token, PEP
   740 attestations), and attaches the built artifacts to its GitHub
   release. `publish-tui-plugin.yml` typechecks, validates the npm payload,
   and publishes through npm OIDC with provenance.

Net effect: type a version once, review one PR, click merge once — both
tags, both GitHub releases, and both package publishes cascade
automatically. Tags are immutable once pushed (see the repo's tag-protection
ruleset), so get the version right before dispatching.

`workflow_dispatch` remains available directly on `publish-pypi.yml` and
`publish-tui-plugin.yml` too, for a manual re-run; both refuse to publish a
version that's already live on their registry.

Neither publish workflow has a stored token — trusted publishing (OIDC) is
required for both; do not replace either with a long-lived
`PYPI_API_TOKEN`/`NPM_TOKEN`.

## One-time setup (already done for this repo)

Kept here as reference for what makes the above possible, not something to
repeat per release.

**PyPI**: a pending publisher was registered on [pypi.org](https://pypi.org)
for project `opencode-swap` (owner `leinardi`, repository `opencode-swap`,
workflow `publish-pypi.yml`, environment `pypi`), and a matching `pypi`
GitHub environment was created (no secrets — it only scopes the OIDC claim).

**npm**: unlike PyPI, npm has no pending-publisher concept — the Trusted
Publisher can only be attached to a package that already exists, so this
needed a one-time manual publish first:

```bash
cd integrations/opencode-tui-plugin
npm login                      # as the personal npm user "leinardi" —
                                # @leinardi is that account's free scope,
                                # no npm Organization needed
bun install --frozen-lockfile
npm publish --ignore-scripts   # publishConfig.access: public already set
npm logout
```

Then, on `https://www.npmjs.com/package/@leinardi/opencode-swap/access`, a
Trusted Publisher was added: GitHub Actions, owner `leinardi`, repository
`opencode-swap`, workflow `publish-tui-plugin.yml`, environment `npm`,
two-factor authentication required with bypass tokens disallowed.

**Repo settings**: "Allow GitHub Actions to create and approve pull
requests" is enabled (Settings → Actions → General → Workflow permissions) —
required for `release.yaml`'s `bump` job to open its PR. This only permits
opening a PR via `GITHUB_TOKEN`; merging still always requires the same
manual bypass-merge as any other PR.
