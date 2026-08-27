# Releasing

This project ships two independently-versioned packages from one repo: the
Python CLI (PyPI) and the optional npm TUI plugin. **Release the CLI first** —
the plugin's own install docs tell users to install the CLI before the
plugin, and `integrations/opencode-tui-plugin/README.md` pins a minimum CLI
version.

Both releases go through this repo's `Release` workflow
(`.github/workflows/release.yaml`, `workflow_dispatch` →
`leinardi/gh-reusable-workflows/.github/workflows/simple-tag-and-release.yaml@v1`),
which creates the git tag and GitHub release at the dispatched commit. Tags
are immutable once pushed (see the repo's tag-protection ruleset) — get the
version right before dispatching.

## Python CLI (PyPI)

### Before first publish

1. On [pypi.org](https://pypi.org), add a pending publisher for project
   `opencode-swap`: owner `leinardi`, repository `opencode-swap`, workflow
   `publish-pypi.yml`, environment `pypi`.
2. Create a GitHub environment named `pypi` in repo settings (no secrets
   needed — it only scopes the OIDC claim).

### Release

1. Bump the version in lockstep:
   - `pyproject.toml` (`project.version`)
   - `src/opencode_swap/__init__.py` (`__version__`)
2. Run `make clean && make verify` locally.
3. Commit the version bump and open a PR (branch protection requires it);
   merge once `verify` is green.
4. Dispatch **Release** with the new semver (e.g. `0.3.0`, with or without
   leading `v`) — this tags `v<version>` at the merged commit and creates a
   GitHub release with generated notes.
5. The tag push triggers `.github/workflows/publish-pypi.yml`: it re-verifies
   the tag matches `pyproject.toml`, runs the test suite, builds the wheel and
   sdist, publishes to PyPI via OIDC trusted publishing (no token, PEP 740
   attestations), and attaches the built artifacts to the GitHub release.

`workflow_dispatch` is also available on `publish-pypi.yml` directly for a
manual re-run; it refuses to publish a version that's already on PyPI.

The workflow has no `PYPI_API_TOKEN`. Trusted publishing is required; do not
replace it with a long-lived token.

## OpenCode TUI plugin (npm)

### Before first publish

1. Reserve npm scope `@leinardi` and confirm `@leinardi/opencode-swap` name.
2. In npm package settings, configure trusted publisher:
    - Provider: GitHub Actions
    - Organization: `leinardi`
    - Repository: `opencode-swap`
    - Workflow: `publish-tui-plugin.yml`
3. Run `make verify` with Bun 1.3.14.
4. Review `npm pack --dry-run` output from
   `make tui-plugin-package-check`.

### Release

1. Update `integrations/opencode-tui-plugin/package.json` version. Keep it in
   step with the CLI version for any release where both ship together;
   `cli.py`'s `schema_version` compatibility contract is what actually lets
   them drift apart later.
2. Regenerate lockfile: `bun install --cwd integrations/opencode-tui-plugin`.
3. Commit version and lockfile changes, open a PR, merge once `verify` is
   green.
4. Tag exact merged commit as `tui-v<package-version>` (via the `Release`
   workflow, or manually).
5. Push tag. `publish-tui-plugin.yml` typechecks, validates payload, then
   publishes through npm OIDC with provenance and creates a GitHub release.

`workflow_dispatch` is also available for intentional manual releases. It
uses the package version in checked-out source and refuses an existing npm
version.

The workflow has no npm token. Trusted publishing is required; do not replace
it with a long-lived `NPM_TOKEN`.
