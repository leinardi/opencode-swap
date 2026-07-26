# TUI plugin release

This project ships Python CLI and optional npm TUI package separately. Do not
publish package until Python CLI installation path is documented and tested.

## Before first publish

1. Reserve npm scope `@leinardi` and confirm `@leinardi/opencode-swap` name.
2. In npm package settings, configure trusted publisher:
   - Provider: GitHub Actions
   - Organization: `leinardi`
   - Repository: `opencode-swap`
   - Workflow: `publish-tui-plugin.yml`
3. Run `make verify` with Bun 1.3.14.
4. Review `npm pack --dry-run` output from
   `make tui-plugin-package-check`.

## Release

1. Update `integrations/opencode-tui-plugin/package.json` version.
2. Regenerate lockfile: `bun install --cwd integrations/opencode-tui-plugin`.
3. Commit version and lockfile changes.
4. Tag exact commit as `tui-v<package-version>`.
5. Push tag. `publish-tui-plugin.yml` typechecks, validates payload, then
   publishes through npm OIDC with provenance and creates GitHub release.

`workflow_dispatch` is also available for intentional manual releases. It
uses package version in checked-out source and refuses an existing npm version.

The workflow has no npm token. Trusted publishing is required; do not replace
it with a long-lived `NPM_TOKEN`.
