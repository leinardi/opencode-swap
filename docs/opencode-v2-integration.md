# OpenCode 2 integration findings

> **Outcome.** opencode-swap was discontinued on 2026-10-14, before the OpenCode 2 plugin these
> findings were gathered for was released. The plugin is at tag `opencode2-plugin-final`; `main`
> ships only a notice (see "On a real installation" for why it has to exist). The open items at
> the end are no longer pursued. The findings themselves still describe OpenCode 2.0.29.

Ground truth for the OpenCode 2 work: the `migrate-to-opencode2` command of the last Python
release and the OpenCode 2 TUI plugin. Every finding below was read from OpenCode's source or
observed in the isolated sandbox (`scripts/oc2-sandbox.sh`), with fake credentials only. When
OpenCode behaviour is in question, check and update this file rather than re-guessing. (The
CLI's equivalent for OpenCode 1.x, `docs/opencode-auth.md`, is in the `v0.5.0` tag.)

Sources:

- OpenCode 2.0.29 (`github.com/anomalyco/opencode`, tag `v2.0.29`, commit `042d0832f5`). Paths
  below without a prefix are relative to that checkout.
- OpenCode 1.19.0 (tag `v1.19.0`, commit `733b2c627e`), cited as `v1:<path>`.
- npm tarballs of `@opencode/plugin`, `@opencode/client` and `@opencode/theme` 2.0.0 to 2.0.29,
  for the type surface (V14).

## The sandbox

`scripts/oc2-sandbox.sh` (`make sandbox`, `make sandbox-v1`) installs OpenCode from npm into
`/tmp/oc2` (OpenCode 2, `@opencode/cli`) or `/tmp/oc1` (`--v1`, `opencode-ai@1.19.0`), and runs
it only with `HOME`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and
`TMPDIR` inside `<prefix>/state`. `OC_SANDBOX_DIR` and `--version` select another prefix and
version (the F6 runs used `/tmp/oc2-2017` and `/tmp/oc2-2020`). `OC_SANDBOX_REGISTRY` points
plugin installs at a local registry.

- **The binary is never run bare.** Both npm packages keep the real binary in a per-platform
  optional dependency and link it from a postinstall script, which npm 11+ blocks unless
  allow-listed. The script runs the platform package's binary directly and prints its path.
- **The environment is cleared.** OpenCode runs under `env -i` with an allowlist (`PATH`, the
  terminal and locale variables, `USER`, `SHELL`) plus the sandbox paths, also inside tmux,
  whose server environment the pane would otherwise inherit. Any provider key in the caller's
  environment would otherwise become an env connection (V8). `OC_SANDBOX_PASS` names variables
  to pass through on purpose, with fake values.
- **`check` proves isolation.** It counts entries in the real `opencode`/`opencode2` data,
  config, state and cache directories that are newer than the sandbox marker, printing counts
  only. It fails while a real OpenCode is running, because that also writes there.
- **`stop` also stops the managed service.** The TUI and every CLI command without `--standalone`
  start a background service on a fixed port, `0xc0de` = 49374 for the `latest` channel
  (`packages/cli/src/services/service-config.ts:46-50`). It outlives the TUI and blocks a second
  sandbox's service ("Managed service port 49374 … is already in use"), as seen when the 2.0.17
  sandbox started while the 2.0.29 service still ran.
- **Plugin publishing goes through a local verdaccio.** `npx verdaccio` in `/tmp/verdaccio`
  allows anonymous publish of the probe packages and proxies everything else to npmjs. The
  sandbox installs from it via `npm_config_registry`: OpenCode loads npm config through
  `@npmcli/config`, which reads the environment and `~/.npmrc` (`packages/util/src/npm-config.ts`,
  `packages/util/src/npm.ts:209-242`).

## V2: legacy `auth.json` import

The import is migration `20260805200742_import_legacy_credentials`
(`packages/core/src/database/migration/20260805200742_import_legacy_credentials.ts`).

- **It runs only when an existing DB is migrated.** `DatabaseMigration.apply`
  (`packages/core/src/database/migration.ts:22-52`) runs the pending migrations only when a
  `session` or `session_v2` table exists (`:29-30`). A fresh DB is bootstrapped from the schema
  with every migration id inserted as completed (`:32-46`), so the import never runs on it. A
  non-empty DB without a session table is fatal (`:31`).
- **OpenCode 1.x and 2 share the DB path.** `<XDG_DATA_HOME>/opencode/opencode.db` on the
  `latest` channel (`packages/cli/src/database-path.ts:4-12`). An upgraded OpenCode 1 install
  therefore always has a session table, and the import runs on the first OpenCode 2 start.
  OpenCode 1.19.0's DB already contains an empty `credential` table.
- **At most one credential per provider.** Each `auth.json` key has trailing slashes stripped
  (`:59`). It is skipped when any credential already exists for that integration (`:62`), so a
  re-run, or a second entry normalizing to the same id, imports nothing.
- **Mapping** (`:64-82`, `:105-109`):
  - `api` becomes `{type: "key", key, metadata}`, where legacy `metadata` must be a string record.
  - `wellknown` becomes `{type: "key", key: token}`, and its origin is merged into the kv entry
    `wellknown:sources` (`:91-101`).
  - `oauth` becomes `{type: "oauth", methodID, refresh, access, expires}`, plus
    `metadata: {accountID, enterpriseUrl}` when the legacy `accountId`/`enterpriseUrl` exist.
  - `methodID` is `chatgpt-browser` for `openai`; `device` for `github-copilot`, `opencode` and
    `xai`; `oauth` for everything else.
- **Labels and activity.** The label is `OAuth` or `API key` (`:83`). The id is a fresh random
  `Credential.ID.create()`, and the row's `active` column is left NULL (`:85-88`), which reads as
  active because it is the integration's only credential (see V7).
- **Undecodable entries are dropped silently** (`:57-58`). In the sandbox, an `xai` entry with
  `expires: 1.5` vanished without a message.
- **A malformed file fails the whole migration.** If `auth.json` is not JSON or not an object,
  the migration fails with "Legacy credential file must contain an object" (`:50-53`). The
  migration runs in the server's database layer, so the server never starts. In the sandbox,
  `auth export --standalone` exited 1 with "Standalone server exited before reporting
  readiness", and the migration stayed pending until the file was fixed, when the next start
  imported it. A missing file is fine (`:48-49`).

Sandbox, with `auth.json` from fake records and `opencode auth export --standalone`:

| DB state | Result |
| --- | --- |
| fresh (no DB) | `[]`; the migration is recorded as completed and `auth.json` is left as is |
| copied OpenCode 1.19.0 DB (has `session`) | 6 credentials imported, `xai` dropped; captured as `tests/fixtures/opencode2_legacy_import.json` (0.4.x line) |
| migration already completed, `auth.json` replaced, one imported row deleted | unchanged: no re-import, and the deleted row stays deleted |

## V3: `opencode auth export` and `import`

- **Both commands exist since 2.0.20.** OpenCode 2.0.17 answers `Unknown subcommand "import"`;
  the command was added in commit `4c33a253aa`, first tagged `v2.0.20`.
- **Export is non-interactive.** `opencode auth export [--standalone|--server URL] [target]`
  prints `credential.list()` as JSON, an array of `{id, integrationID, label, active, value}`
  with the secrets in `value` (`packages/cli/src/commands/handlers/auth/export.ts:12-22`). It
  needs no TTY; on a TTY it only adds a stderr warning (`:18-21`). Errors go to stderr with exit 1
  (`:24-28`). The sandbox exported with stdin from `/dev/null`, exit 0, and an unreachable
  `--server` gave exit 1 with "Could not reach server".
- **Export runs pending migrations.** The CLI never opens the DB itself; it talks HTTP to a
  server (`packages/cli/src/commands/handlers/auth/shared.ts:8-11`), and that server's database
  layer runs the migrations (`packages/core/src/database/database.ts:49`). The V2 "copied DB" run
  was an `auth export`.
- **Server choice.** Without flags the CLI ensures the managed background service and reuses a
  running one even when its version differs (`mismatch` defaults to `"ignore"`,
  `packages/cli/src/services/server-connection.ts:45-46`). `--standalone` spawns a private
  `serve --stdio` child instead (`packages/cli/src/services/standalone.ts:17-37`).
- **Generic API.** `opencode api credential.list [--standalone]` prints `{"data": [...]}` and
  exits 1 with `HTTP <status>` on a non-2xx response (`packages/cli/src/commands/handlers/api.ts`).
  Labels can only be changed this way:
  `opencode api credential.update --param credentialID=<id> -d '{"label": "…"}'`.
- **Import** (`packages/cli/src/commands/handlers/auth/import.ts`):
  - reads a file or stdin, and refuses a TTY stdin (`:16-17`);
  - decodes strictly as `Array<Credential.Entry>` and fails on anything else (`:19`); a
    `{"type": "weird"}` value gave "Expected Credential.Value" and exit 1;
  - **skips any id that already exists, without comparing values** (`:25`), and treats a 409 as
    skipped (`:41`);
  - keeps `id` and `label` (`:29-31`);
  - passes `activate: active && <integration not in the destination>` to `credential.create`
    (`:35`). The set of destination integrations is computed once, before the loop (`:23`).
- **`credential.create` has the final say on activation.** It activates when
  `input.activate !== false || !current`, i.e. also when the integration has no credential yet
  (`packages/core/src/credential.ts:124`). An inactive create next to a NULL-active legacy row
  pins that row active first (`:131-133`). So for an integration new to the destination:
  - its **first** imported entry is activated even with `active: false`;
  - every `active: true` entry is activated in turn, and the **last** one wins.

  Sandbox, importing `key` entries in file order:

  | Integration | Entries (`active`) | Active afterwards |
  | --- | --- | --- |
  | `mistral` (new) | `a` (false), `b` (true) | `b` |
  | `groq` (new) | `c` (false) | `c` |
  | `cohere` (new) | `d` (true), `e` (true) | `e` |
  | `zai-coding-plan` (has a NULL-active legacy row) | `f` (false) | the legacy row, now stored with `active = 1` |

  A generated import file therefore controls the active credential of a new integration only
  if it lists exactly one `active: true` entry per integration, and lists it first.
- **Generated ids are accepted.** `Credential.ID` is a branded string with no format check
  (`packages/schema/src/credential.ts:11-15`), and nothing decodes ids. The sandbox imported
  `cred_0123456789ABCDEFGHIJabcdef` and `cred_newintegration0000000000`; a second import printed
  `Imported 0 credentials, skipped 4 already present`.
- **Labels and `active` after import**: imported labels (`work`, `personal`) were kept. Entries
  for integrations that already had a credential stayed inactive, and the new `deepseek`
  integration adopted its `active: true` entry.

The value schema (`packages/schema/src/credential.ts:32-70`):

| `type` | Fields |
| --- | --- |
| `oauth` | `methodID`, `refresh`, `access`, `expires` (non-negative integer), `metadata?` (record) |
| `key` | `key`, `metadata?` (record), `configuration?` (`Form.Answer`) |
| `external` | `methodID`, `metadata?` (record); no secret, renewed outside the store |

OpenCode's own ids are `cred_` plus 12 hex characters of `timestamp * 0x1000 + counter` and 14
characters from `0-9A-Za-z` (`packages/schema/src/identifier.ts:3-31`).

## V4 and V5: installing and loading a TUI plugin

- **Module shape.** The entry is the package's `./tui` export (`packages/plugin/src/host.ts:17-44`).
  Its `default` export must be an object with a non-empty string `id` and a function `setup`,
  or loading fails with `Invalid V2 TUI plugin module: <spec>`
  (`packages/tui/src/plugin/context.tsx:694-695,741-751`). There is no OpenCode 1 adapter: the
  sandbox showed exactly this error for `@leinardi/opencode-swap@0.4`.
- **A `"."` export changes where `plugin add` writes.** If the package has a server entry,
  `opencode plugin add` registers it in `opencode.json` as a server plugin
  (`packages/cli/src/commands/handlers/plugin/add.ts:22-50`).
- **Config.** TUI plugins live in `<XDG_CONFIG_HOME>/opencode/cli.json` under `"plugins"`, as a
  string or `{package, options}` (`packages/cli/src/config/config.ts:31`). `opencode plugin add`
  wrote `"plugins": ["oc2-probe-plugin"]` there.
- **Specs.** `plugin add` rejects anything but a registry or Git spec (`add.ts:17-18`); only
  `file://`, `./`, `../` and absolute paths count as local, and a local path must be a directory
  (`packages/plugin/src/source.ts:61-66`). A tarball is therefore tested through a local
  registry.
- **A local directory loads, and is transformed.** For a local plugin the entry is
  `<dir>/tui` resolved as a module (`packages/plugin/src/host.ts:17-44`). In the sandbox,
  `/tmp/oc2-spike/probe-local` (a raw `tui.tsx`, not precompiled), listed directly in `cli.json`
  `"plugins"`, loaded next to the npm probe, and its footer counter advanced every second. It
  has to be added to `cli.json` by hand, since `plugin add` rejects it.
- **A local directory's `package.json` is not read.** Without a package name, `Host.resolve`
  looks for the plain path `<dir>/tui` (`packages/plugin/src/host.ts:19-24`), so `exports` is
  ignored. Listing this repository's root in the sandbox's `cli.json` loaded nothing (`/swap`:
  "No matching commands"). A directory holding only a `tui.js` that re-exports the root's
  `dist/tui.js` loaded, and `/swap` listed the fake accounts.
- **Installation.** Arborist `reify` with the user's npm config, `audit: false` and
  `ignoreScripts: true`, without `legacyPeerDeps` (`packages/util/src/npm.ts:209-242`). The cache
  is `<XDG_CACHE_HOME>/opencode/npm/<name>@<spec>/<generation>/` (`npm.ts:141,302,320`).
- **No automatic refresh.** A loaded package plugin is never re-resolved during a session
  (`context.tsx:663-666`), and `add` reuses an existing generation without network
  (`npm.ts:256-260`). `opencode plugin check` reported `oc2-probe-plugin 1.0.0 (update
  available)`; `opencode plugin update` installed 1.0.1 as a new generation, picked up on the next
  TUI start.
- **The `/plugins` dialog updates server plugins only.** Its `update` action (`ctrl+u`,
  `dialog.plugins.update`, `packages/tui/src/config/keybind.ts:281`) is shown only for an entry
  that `outdated()` accepts, which requires `runtime === "server"` and a package source
  (`packages/tui/src/feature-plugins/system/plugins.tsx:43,304-305`); its "check for updates"
  action is likewise hidden unless a server package plugin is configured (`:212-221`). A TUI-only
  plugin is updated with `opencode plugin update`, which installs TUI targets through npm
  (`packages/cli/src/commands/handlers/plugin/update.ts:23-29`).
- **Precompiled Solid output renders reactively.** The probe, built per file with
  `@opentui/solid`'s `transformSolidSource`, showed a footer counter that advanced every second.
- **Raw `.tsx` from npm does not load.** A package exporting `./src/tui.tsx` failed with "Cannot
  find package '@opentui/solid'" from inside the plugin's cache directory, so precompiling
  stays mandatory.
- **Peers can be optional.** With `@opentui/core`, `@opentui/solid` and `solid-js` declared
  optional, Arborist installed nothing but the plugin itself, and it still rendered: the host
  provides `solid-js`, `@opentui/*`, `effect` and `@opencode/plugin/*`. Loading a plugin-local
  copy of `effect` or `@opencode/plugin` throws "plugins must use the host's …"
  (`packages/plugin/src/runtime.ts:28-33`). Nothing reads `peerDependenciesMeta`.
- **`engines` is ignored by OpenCode 2.** No code checks `engines.opencode`.

## V6: footer slot, dialogs and theme

- **`prompt.footer`.** `ui.slot({append: "prompt.footer", render})` rendered the probe's text
  inline after the built-in hints (`/ shift+tab agents  ctrl+p commands …`). Text that does not
  fit wraps to the next line. The slot input is `{sessionID?, mode, showDetails}`
  (`packages/plugin/src/tui/context.ts:159-163`). Claims at one anchor follow plugin enable
  order, built-ins first (`context.ts:205-218`).
- **`ui.dialog.select`.** Options are grouped under their `category` in first-seen order, with
  `title` and `description` on one line and `footer` right-aligned. `current` exists since
  2.0.0; `search` and `actions` only since 2.0.23. `ui.dialog.confirm` resolves `true`, `false`,
  or `undefined` on dismissal.
- **Theme.** `theme.text.feedback.{error,warning,success,info}.{base,muted}` (RGBA) has existed
  since 2.0.0 (`@opencode/theme` `dist/tui/types.d.ts`). The probe's footer used
  `feedback.success.base`.
- **Keymap layers must use `mode: "global"`.** A layer created in `setup` with the default mode
  (`base`) registered commands that the prompt's slash completion never offered ("No matching
  commands"). With `mode: "global"` they were offered and ran. On 2.0.17, `keymap.layer` called
  from `setup` threw "Keymap.Provider is missing". Inside a `ui.slot({append: "app"})` render it
  worked on both versions, which is how the built-ins do it
  (`packages/tui/src/feature-plugins/system/stats.tsx:182-209`). Palette and slash commands need
  an `id` (`packages/tui/src/context/keymap.tsx:253-275`).

## V7: credentials and events

- **`client.credential.list()`** returns every stored credential as `{id, integrationID, label,
  active, value}`, values included (`packages/server/src/handlers/credential.ts:57-66`). The
  sandbox list matched `auth export`. The client also has `create`, `update`, `activate`
  (`{credentialID}`) and `remove`.
- **One credential per integration is active.** It is the row with the highest
  `(active, time_created, id)`, so NULL-active legacy rows fall back to the newest
  (`packages/core/src/credential.ts:122-133,182`).
- **Events** (`packages/schema/src/credential.ts:18-30`):
  - `credential.switched` carries `{integrationID, credentialID | null}`;
  - `credential.updated` carries **no payload**, so it cannot say which integration changed;
  - both are published globally.
- **Who publishes what** (`packages/core/src/credential.ts`):
  - `create` publishes `updated`, plus `switched` when it activates (`:163-169`);
  - `activate` publishes `switched`, and is a no-op for the already active credential
    (`:177-205`);
  - `update` publishes `updated` **only for a label change**; a value-only update emits nothing
    (`:250-256`);
  - `remove` publishes `updated`, plus `switched` when a replacement is promoted (`:316-322`).
- **Sandbox transcript** (probe log, secret-free):
  - `/probe-pick`, then confirm, then `client.credential.activate` produced
    `credential.switched {integrationID: "zai-coding-plan", credentialID: "cred_0123…"}`;
  - `opencode auth switch zai-coding-plan <id>` from another terminal, through the shared
    service, produced the same event in the TUI;
  - the same switch with `--standalone` ran in a private server: the DB changed and the TUI got
    **no** event;
  - a label change via `opencode api credential.update` produced `credential.updated {}`.
- **Stale client.** `context.client` is captured once, and a reconnect replaces the API object
  (`packages/tui/src/plugin/api.tsx:159`, `packages/tui/src/context/client.tsx:27-32,46-47`).

## V8: providers, integrations and env connections

- **Provider to integration.** A provider's integration is `provider.integrationID ?? providerID`
  (`packages/core/src/model-resolver.ts:376-378`, `packages/core/src/provider.ts:423`). In a
  plugin, `data.location.provider.list()` gives `{id, integrationID}`. In the sandbox `openai`,
  `zai-coding-plan`, `opencode`, `deepseek` and `github-copilot` all mapped to themselves. The
  Console sets `integrationID: "opencode"` on its own providers too
  (`packages/core/src/plugin/provider/opencode.ts:265-271`), so the mapping must go through the
  provider list rather than assume equality.
- **z.ai's integration id is `zai-coding-plan`** (F3 not needed). Its env var is
  `ZHIPU_API_KEY`; OpenAI's is `OPENAI_API_KEY`, and OpenCode Console's `OPENCODE_API_KEY`
  (`packages/core/src/models-dev/snapshot.txt`).
- **Env connections.** `IntegrationInfo.connections` lists stored credentials first, the active
  one first, then the env vars set in the **server's** environment
  (`packages/core/src/integration.ts:396-410`). The active connection is `connections[0]`
  (`:719-722`), so a stored credential always beats an env var, and an env connection is active
  exactly when `connections[0].type === "env"`. `credential.list()` never contains env entries.
  Sandbox, with fake `DEEPSEEK_API_KEY` and `MOONSHOT_API_KEY` passed through: `deepseek` listed
  two stored credentials, then `{type: "env", name: "DEEPSEEK_API_KEY"}`; `moonshotai` listed
  only the env connection.

## V9: the current session's provider

- `data.session.get(id).model` (`{id, providerID, variant?}`) is the committed model. It is set
  by the `session.model.selected` event, which the prompt emits on submit
  (`packages/client/src/solid/data.ts:705-708`, `packages/tui/src/component/prompt/index.tsx:1283-1290`),
  so it is undefined until the session's first prompt.
- Assistant messages carry the model actually used; `model-switched` messages are only inserted
  once the session's messages are loaded (`data.ts:709-716`).
- `ui.model.current()` is the prompt's selection for the routed session, or the new-session
  model on the home route (`packages/tui/src/plugin/api.tsx:282-287`). It exists since 2.0.17.
- Sandbox: on the home route the probe saw no session and
  `ui.model.current() = {providerID: "github-copilot", …}`. `--session <new id>` stays on home
  until the first prompt. A committed session model was not exercised, because submitting a
  prompt calls the real provider endpoint.
- **Conclusion for F4:** use the plan's chain unchanged: `session.get(id).model.providerID`, then
  the last assistant or `model-switched` message, then `ui.model.current()`.

## V12: the background service and token refresh

- The service is `opencode serve --service`, spawned or discovered by `Service.ensure`
  (`packages/cli/src/services/service-config.ts:111-136`). The TUI and CLI commands share it
  unless `--standalone` or `--server` is given.
- **Refresh is lazy and active-only.** `Integration.connection.resolve` refreshes only the
  connection being resolved, only when `expires <= now + 5 min`, and stores the result with a
  value-only `update` (`packages/core/src/integration.ts:723-741`). Callers resolve only the
  active connection (`:719-722`). There is no background refresh loop.
- **Some startup rewrites touch inactive credentials.** Provider plugins rewrite every matching
  credential with value-only updates, so no event fires:
  - xai `browser` becomes `device` (`packages/core/src/plugin/provider/xai.ts:80-89`);
  - openai `chatgpt-browser` becomes `chatgpt-token-sharing` with `metadata.models` stripped, but
    only when the metadata has a `clientID` (`packages/core/src/plugin/provider/chatgpt.ts:29,85-91,239-258`).
    Imported legacy OpenAI credentials have none; in the sandbox they kept `chatgpt-browser`.
- **Consequence for the plugin:** a credential's value can change without an event (refresh,
  these rewrites, or a `--standalone` client), so usage caching must key on the value's revision.
  That is the plan's revision tracker.

## V13: OpenCode 1.19 and the version split

- **OpenCode 1.19 enforces `engines.opencode`** for npm plugins before importing them
  (`v1:packages/opencode/src/plugin/loader.ts:124-130`,
  `v1:packages/opencode/src/plugin/shared.ts:194-206`). In the sandbox, a probe with
  `"engines": {"opencode": ">=2.0.0 <3"}` was installed but never imported. The same probe
  published with `">=1.0.0"` was imported (its module side effect ran).
- **A bare package name means `@latest` in OpenCode 1** (`v1:packages/opencode/src/plugin/shared.ts:210`),
  so after a 1.0.0 release, OpenCode 1 users who list `@leinardi/opencode-swap` would get the
  OpenCode 2 build and have it rejected. They need a pin such as `@0.5`.
- **`@leinardi/opencode-swap@0.4` loads under 1.19.** In `tui.json` it resolved 0.4.6 and
  registered `/swap`, `/swap-next` and `/swap-refresh`.

## V14: the `./tui` type surface and the minimum version (F6)

From the `@opencode/plugin` 2.0.x tarballs (`dist/tui/context.d.ts`) and `@opencode/client`
(`dist/promise/generated/client.d.ts`):

| API | First 2.0.x |
| --- | --- |
| `Plugin.Definition {id, setup}`, `ui.slot` with `prompt.footer`, `ui.dialog.select` (`footer`, `category`), `ui.dialog.confirm`, `ui.toast`, `keymap.layer` (`mode`), `data.on`, `data.session.get`, `data.session.message.list`, `data.location.provider`/`integration`, `theme` | 2.0.0 |
| `ui.model.current()` | 2.0.17 |
| `client.credential.list()` and `create()` | 2.0.20 |
| `opencode auth export` / `auth import` | 2.0.20 |

- On 2.0.17 the host's `client.credential.list` is undefined at runtime (the probe's call threw
  a `TypeError`), and `client.credential` has only `update`, `activate` and `remove`.
- On 2.0.20 the probe loaded and rendered its footer, `client.credential.list()` returned the
  stored credentials, and `credential.switched` arrived.
- **F6 minimum: 2.0.20.** It is the oldest 2.0.x that installs in the sandbox and has every API
  used. The plugin's `engines.opencode` is `>=2.0.20 <3`; OpenCode 2 ignores it (V4/V5), but
  OpenCode 1 uses it to reject the package (V13).

## Migration end to end

`migrate-to-opencode2` against the sandbox (0.4.x vault built with `opencode-swap add` from fake
`auth.json` records; `openai/home`, `zai-coding-plan/work`, and `openai/work` live, rotated from
`R0` in the vault to `R1` in `auth.json`). Each run wrote `--output`, ran
`opencode auth import --standalone` on it, then checked `auth export` and
`auth list --format json`:

| DB state | Migration output | Afterwards |
| --- | --- | --- |
| fresh | all 3 accounts; `openai/work` (from `R1`) and `zai-coding-plan/work` active | 3 credentials, `work` active with `R1` |
| fresh, re-run | nothing new: "already present" for all 3 | `Imported 0 credentials`; still 3 |
| copied 1.19 DB, legacy import pending | only `openai/home`; `openai/work` and `zai-coding-plan/work` "already in OpenCode 2 as 'OAuth'/'API key'" | 3 credentials: the legacy `OAuth` (with `R1`), `home`, `API key` |
| migration completed, re-run | nothing new | `Imported 0 credentials`; still 3 |

Every account was present exactly once in every state, and the vault's files were
byte-identical before and after.

## Plugin end to end

The rewritten plugin, packed and published to the local verdaccio as `1.0.0-e2e.1`, listed in
`cli.json` `"plugins"` next to the event-logging probe. Credentials were fake and imported with
`auth import --standalone`: two OpenAI OAuth entries (`work`, `home`) whose access tokens are
unsigned JWTs with made-up emails, and two z.ai keys (`team`, `solo`).

| Check | 2.0.29 | 2.0.20 |
| --- | --- | --- |
| Plugin loads; footer hidden for the free OpenCode model (no stored credential) | yes | yes |
| After `/models` → an OpenAI model, footer reads `work · n/a` (fake token refused) | yes | yes |
| `/swap` lists `home home@example.com · n/a`, `work work@example.com · active`, `solo …bbbb · n/a`, `team …aaaa · active`, grouped by integration | yes | yes |
| Picking `home`, confirming: toast "openai now uses home.", footer `home · n/a`, probe logged `credential.switched {openai, cred_…home…}`, prompt model unchanged | yes | yes |
| `/swap-next` confirms "Use work for openai?", switches back, `credential.switched` logged | yes | yes |
| `/swap-refresh` toasts "Accounts and usage refreshed." | yes | yes |
| A GLM model moves the footer to `team · n/a` | yes | not run |
| No planted token, key or JWT payload in the tmux capture or `opencode.log` | yes | yes |
| `oc2-sandbox.sh check` afterwards | passed | passed |

- **The packaging guards, each broken once** on a scratch copy of the package, all failed as
  intended:

  | Break | Guard | Failure |
  | --- | --- | --- |
  | `exports["./tui"]` pointing at `./src/tui.tsx` | `check-tui-entry-compiled.sh` | "entry must be compiled .js" |
  | an added `"."` export | `check-tui-entry-compiled.sh` | "package.json exports must be exactly ./tui" |
  | `import { Effect } from "effect"` appended to `dist/state.js` | `check-tui-entry-compiled.sh` | "runtime import of effect or @opencode/*" |
  | `import … from "@opencode/plugin/tui"` appended to `dist/usage/zai.js` | `check-tui-entry-compiled.sh` | "runtime import of effect or @opencode/*" |
  | a `jsx-runtime` import appended to `dist/footer.js` | `check-tui-entry-compiled.sh` | "use the generic JSX runtime" |
  | the default export's `setup` renamed | `check-tui-entry-compiled.sh` | "must have a string id and a function setup" |
  | `"./bands.js"` turned into `"./bands"` in `dist/state.js` | `check-tui-entry-compiled.sh` | "relative imports above lack their .js extension" |
  | a required `solid-js` `1.8.x` peer next to `@opentui/solid` 0.5.17 (which pins `solid-js` 1.9.12) | `check-npm-resolution.sh` | `ERESOLVE: unable to resolve dependency tree` |

  Bun resolves an extensionless relative import on its own, which is why the last
  `check-tui-entry-compiled.sh` row needed its own grep rather than the import of the entry.
- **The footer's first lookup.** A second build (`1.0.0-e2e.2`) looks usage up as soon as a
  footer mounts, not only when its integration changes: home and every session render their own
  prompt, so navigating mounts a fresh footer. On 2.0.29 it showed `work · n/a` right after the
  model switch, with no planted value in the capture or the log.
- **2.0.17** (no `credential.list`): one toast "opencode-swap needs OpenCode 2.0.20 or later.",
  and `/swap` found "No matching commands".
- **OpenCode 1.19** with `@leinardi/opencode-swap@1.0.0-e2e.1` in `tui.json`: the package was
  installed into the plugin cache but never registered a command, the `engines.opencode` gate of
  V13. With `@leinardi/opencode-swap@0.4` it resolved 0.4.6 and offered `/swap`, `/swap-next`
  and `/swap-refresh`.

## The plugin at the repository root

The plugin moved from `integrations/opencode-tui-plugin/` to the repository root. Its make
targets are now `plugin-*`; the `tui-plugin-*` names stay as aliases without help text, so
`make help` lists each target once. The published payload is unchanged apart from `README.md`
and `LICENSE`, which are now the repository's own.

`auto-tag-release.yaml` was replayed on a scratch clone: its "Detect a version bump" script,
extracted from the workflow, ran with `PACKAGE_JSON: package.json` against the move commit and
then against a follow-up commit bumping the version.

| Commit | Output |
| --- | --- |
| the move (`package.json` 0.4.6, absent from the parent) | `The parent commit has no package.json - not a release commit.`, `bumped=false` |
| a follow-up bump to 1.0.0 | `Version changed: 0.4.6 -> 1.0.0.`, `bumped=true`, `version=1.0.0` |

On `main` the move lands in a merge commit whose first parent still has the plugin under
`integrations/`, so the same check applies there: the move tags and publishes nothing.

## On a real installation

On 2026-10-14 the maintainer's machine moved from OpenCode 1.19.0 to 2.0.29 with real accounts,
at the maintainer's request, after a backup of every OpenCode and opencode-swap directory:
OpenCode's own installer (`install --version 2.0.29 --no-modify-path`), `migrate-to-opencode2`
run from this branch's history, then the plugin's build through a local wrapper directory.
Nothing below quotes a credential.

- **The migration matched the sandbox.** OpenCode 2's first start (the migration's `auth export`)
  imported OpenCode 1's two live logins, labelled `OAuth` (openai) and `API key`
  (zai-coding-plan), plus an `opencode` API key. `migrate-to-opencode2` reported both live
  accounts "already in OpenCode 2" and exported the third saved account, which `auth import`
  added.
- **Real usage renders.** With an OpenAI model selected, the footer read
  `OAuth · 5h 0% @Oct 15, 03:09 | 7d 2% @Oct 21, 14:10` for the legacy-imported credential (a
  `chatgpt-browser` one, as imported legacy OpenAI credentials keep that method, V12). The
  migrated account, whose access token had expired in the vault, showed `n/a`, as designed: the
  plugin never refreshes a token.
- **Neither OpenAI token carried an email claim**, so both OpenAI rows in `/swap` had no
  description. The legacy-imported one is labelled only `OAuth`.
- **OpenCode 2 turns OpenCode 1's `tui.json` into `cli.json`**, once, on its first config load,
  and only while `cli.json` does not exist (`packages/cli/src/config/migrate.ts:37,100-117`). It
  carries over the theme and display settings from `tui.json` and `kv.json`, and the plugins:
  every `plugin` entry, and every `plugin_enabled` id as a bare entry (`:160-166`). This
  machine's `tui.json` (`"plugin": ["@leinardi/opencode-swap"]`, `"plugin_enabled":
  {"opencode-swap": true}`) became `"plugins": ["@leinardi/opencode-swap", "opencode-swap"]`.
- **A migrated `"opencode-swap"` entry can install someone else's package.** The TUI handles
  `plugins` in order; a bare name only enables an already resolved plugin whose id matches, and
  otherwise is resolved and installed as an npm spec (`packages/tui/src/plugin/context.tsx:318-350`).
  npm has an unrelated `opencode-swap` package (0.1.3). Whenever `@leinardi/opencode-swap` fails
  to load first (the 0.4.x build does, with "Invalid V2 TUI plugin module", V4/V5), that package
  would be installed and run in the TUI. Here the entries were replaced before the TUI first
  started, and OpenCode 2's plugin cache (`~/.cache/opencode/npm`) did not exist afterwards, so
  nothing was installed.

## Deviations and open items

Deviations from the plan, each with its evidence above:

- **F1 not triggered.** `opencode auth export` runs non-interactively (V3), so
  `migrate-to-opencode2` has no `--destination` option. It runs `auth export --standalone`, so it
  neither starts a background service nor talks to an already running service of another version.
- **F2 not triggered.** Optional peers install and render (V4/V5).
- **F3 not triggered.** z.ai's integration id is `zai-coding-plan` (V8).
- **F6 resolved to 2.0.20** (V14). `auth export`/`auth import` need 2.0.20 as well, so the
  migration should refuse an older OpenCode 2 with a message naming 2.0.20, rather than only
  checking for major version 2.
- **The migration must order its output.** The plan sets `active: true` only on the
  registry-active account of an integration the destination lacks. Since the first entry created
  for a new integration is activated regardless (V3), the migration lists that entry first in
  its integration's group. An integration whose registry-active account is not emitted gets its
  first emitted entry activated by OpenCode.
- **Keymap layers** are registered inside an `app` slot render with `mode: "global"` (V6). The
  plan says only `keymap.layer`.
- **`credential.updated` has no payload** (V7), so "switch/update events for that integration"
  in the plan's cache rules cannot be scoped: an update event invalidates every integration.
- **The usage cache is fresh for 55 s, not 60 s.** An entry is stamped when its request starts
  and expires 5 s (the list timeout) early. With exactly 60 s, a poll whose list ran faster than
  the previous one would still find the previous result fresh, and the footer would refresh only
  every other poll.
- **`src/bands.ts` was a copy until the rewrite.** The 0.4.x build compiled `src/tui.tsx` alone
  into `dist/tui.js`, so the TypeScript ports left it untouched; the rewrite replaced it and its
  copy of the band logic.
- **The plugin is typechecked against `@opencode/plugin` and `@opencode/theme` 2.0.20**, the F6
  minimum, so a member newer than that fails `tsc`.
- **`engines` lists only `opencode`.** The old `node` entry is gone: the plugin runs in
  OpenCode's Bun runtime.
- **`sessionStatus` carries the session's integration as well as its run state.** `/swap-next`
  and the footer poll need the current integration (the F4 chain plus the provider list), and
  the confirm text notes a request in progress.
- **The TypeScript usage lookups differ from the 0.4.x CLI's `usage.py` in three ways.**
  - Every failure message is fixed text. `usage.py` printed a network error's own message, which
    the plugin never does.
  - Redirects are not followed, so a credential only ever goes to the two usage endpoints; a
    redirect reports its HTTP status.
  - `JSON.parse` rejects the `NaN`/`Infinity` literals Python's `json` accepts, so such a body is
    invalid JSON rather than a snapshot with that window dropped.
- **One unintended write to the real OpenCode log.** Before the sandbox environment existed, the
  freshly installed 2.0.29 binary was run once as `opencode --version` with the real
  environment. It appended to `~/.local/share/opencode/log/opencode.log`; the modification time
  matched that call and nothing else changed. The file was not read or edited. The script now
  runs OpenCode only inside the sandbox, and the header comment records why.
- **Isolation checks.** Markers created before 17:37 on 2026-10-14 reported changes in the real
  directories, coinciding with a concurrently running real OpenCode 1.x. A clean 2.0.29 cycle
  afterwards (legacy-import seed, `auth export`, TUI with the probe plugin, stop) passed `check`,
  as did the 2.0.20 cycle.

- **The README points updates at `opencode plugin update` only.** The plan named
  `opencode plugin update` or `ctrl+u`; `ctrl+u` in `/plugins` never offers to update a TUI-only
  plugin (V4/V5).
- **The README has no screenshots.** The GIFs and PNGs in `assets/` show the 0.4.x plugin in
  OpenCode 1's session prompt, not the OpenCode 2 footer, so the README describes the footer in
  text. They stay in the repository until replaced.

Open items for the human checklist:

- **F5: `wham/usage` with `chatgpt-token-sharing` tokens** needs a real login. OpenCode 2's
  default OpenAI login stores that method. Until it is checked, every OpenAI OAuth method is
  eligible for usage, and failures render `n/a` and back off.
- **A committed session model (V9)** was not exercised in the sandbox. The F4 chain is
  implemented as planned; a real session should confirm the footer follows `session.get().model`.
- **`credential.updated` without a payload** means the plugin cannot tell which integration a
  label change touched. It re-lists credentials on every update event, which is cheap; confirm
  it causes no visible flicker.
- **A stale `context.client` after a reconnect (V7).** The plugin cannot reach the replacement
  client. A list that never settles times out after 5 s and shows as an error rather than
  blocking the plugin, but a reconnect during a session should be tried with a real service
  restart.
- **Real usage numbers:** seen for a legacy-imported OpenAI credential (On a real
  installation). Still to see: z.ai, an OpenAI credential after OpenCode refreshes it, and the
  pace colours away from green.
- **No email for real OpenAI tokens.** Both real access tokens lacked the claim, so OpenAI rows
  show only their label, and a legacy-imported one reads `OAuth`. The README could tell users to
  rename it in `/connect` (`ctrl+r`); whether a credential's `metadata` carries an email is
  unchecked, and would need checking without printing values.
- **The migrated `"opencode-swap"` plugin entry** (On a real installation). Every 0.4.x user
  with the plugin enabled in `tui.json` gets it in `cli.json`, next to whatever
  `@leinardi/opencode-swap` spec they had; with `@0.4` (as the 0.4.x README says), the first
  load fails and the unrelated npm package is installed. The README's migration steps now say to
  fix `cli.json` before the first start. Since then `migrate-to-opencode2` (released as 0.5.0)
  prints the same warning, and the 1.x notice keeps the id. Still worth it: an OpenCode issue to
  migrate `plugin_enabled` ids as enable-only.
- **New screenshots** of the OpenCode 2 footer and `/swap` picker, with real usage, to replace
  `assets/` and go back into the README.
- **The `legacy` dist-tag:** point it at 0.5.0 once 1.0.0 is out (`docs/releasing.md#sunset`).
