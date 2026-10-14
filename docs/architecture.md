# Architecture

`@leinardi/opencode-swap` is an OpenCode 2 TUI plugin. OpenCode 2 stores every account itself
(the `credential` table of `opencode.db`) and already switches, renames, removes, exports and
imports them. The plugin adds only what OpenCode lacks: a footer with the active account and its
usage, the `/swap` cross-provider picker, `/swap-next` and `/swap-refresh`.

Every OpenCode behaviour this document relies on is recorded, with source citations and sandbox
evidence, in [`opencode-v2-integration.md`](opencode-v2-integration.md) (sections `V2` to `V14`
below refer to it). When OpenCode's behaviour is in question, check and update that file rather
than re-deriving it from the code here.

Up to 0.4.x this repository was a Python CLI with its own credential vault for OpenCode 1. That
code now lives on the `maint/0.4` branch; see [Releasing](releasing.md#the-04x-line).

## Tech stack

- **TypeScript on Bun.** OpenCode 2 runs plugins in its own Bun runtime. Development and CI
  pin Bun 1.3.14.
- **Solid, through `@opentui/solid`.** The footer is a Solid component. The host provides
  `solid-js`, `@opentui/*`, `effect` and `@opencode/plugin` at runtime (`V4/V5`), so the
  plugin's peers are optional and it installs nothing but itself.
- **Types only from `@opencode/plugin` and `@opencode/theme`**, pinned at 2.0.20 (the minimum
  OpenCode version) as dev dependencies, so a member newer than the minimum fails `tsc`. OpenCode
  refuses a plugin-local copy of `effect` or `@opencode/plugin`, so neither may be imported at
  runtime.
- **Tests with `bun test`**, against injected fakes; nothing starts OpenCode (see
  [Testing](testing.md)).

## Module map

```text
src/
  tui.tsx          the `./tui` entry: {id, setup}; wires the context into the store, the footer
                   slot and the command layer, and tears all of it down on dispose
  features.ts      runtime feature detection and the minimum OpenCode version (2.0.20)
  state.ts         AccountStore: credential list, revision tracker, usage cache, request
                   ownership, back-off, polling, events, the picker and activation
  accounts.ts      the secret-free projection (AccountView), sort order, next account,
                   provider-to-integration resolution, usage eligibility, footer account
  session.ts       the current session's provider and integration, env connections
  commands.ts      the keymap layer: /swap, /swap-next, /swap-refresh
  footer.tsx       the prompt.footer component and its pace colours
  bands.ts         window labels, reset times and pace bands for usage windows
  options.ts       plugin options (usage, debug)
  jwt.ts           JWT claim decoding for the OpenAI account id and email
  usage/
    http.ts        fetchJson: timeouts, no redirects, fixed-text failures
    openai.ts      ChatGPT wham/usage request and parser
    zai.ts         z.ai GLM Coding Plan quota request and parser
    types.ts       UsageSnapshot and UsageWindow, and their validation helpers
    index.ts       fetchUsage: the one dispatch from a UsageRequest to its endpoint
scripts/
  build.ts         per-file Solid transform of src/ into a mirrored dist/
  check-tui-entry-compiled.sh   the packed payload is precompiled, ./tui-only, type-only
  check-npm-resolution.sh       OpenCode's Arborist resolves the packed payload's peers
  check-license-headers.sh      every source carries the MIT header
  oc2-sandbox.sh                an isolated OpenCode install for integration work
```

Most new code belongs in an existing module. `state.ts` owns everything that schedules or
outlives a call: timers, events, request lifetimes and dialogs. `usage/*` performs one request;
the other modules are pure functions.

## Startup

`setup(context)` first runs `missingFeatures` (`src/features.ts`): every context member the
plugin calls, checked at runtime. If one is missing (OpenCode 2.0.17 has no
`client.credential.list`, `V14`), it shows one toast, "opencode-swap needs OpenCode 2.0.20 or
later.", and registers nothing. `engines.opencode` cannot do this: OpenCode 2 ignores it
(`V4/V5`); it only keeps OpenCode 1 from loading the package (`V13`).

Otherwise it builds one `AccountStore` from the context, then:

- appends the `Footer` to the `prompt.footer` slot (`V6`);
- appends to the `app` slot a render that registers the command layer with `keymap.layer`, in
  `mode: "global"`, and renders nothing. From `setup` itself the layer either never reaches slash
  completion (default mode) or throws on 2.0.17 (`V6`);
- starts the store: the first list, the 60 s poll and the event subscriptions.

The function `setup` returns removes the slots and the layer and disposes the store.

## Credentials and the secret boundary

`client.credential.list()` returns every stored credential with its value: OAuth tokens and API
keys (`V7`). The values never reach UI state:

- **`AccountView`** (`src/accounts.ts`) is the only shape in exposed state: id, integration,
  label, active, kind, and for display the OpenAI email (from the access token's claims) or a
  static key's last 4 characters (`…abcd`, keys of 20 or more characters only). It is built field
  by field, never by spreading the entry, so a field OpenCode adds later cannot ride along.
- **`usageRequest(entry)`** reads the secret a usage lookup needs from the live entry at request
  time and hands it to that one request. Nothing stores it.
- **A value the plugin does not recognize** (an unknown credential type, or a known type
  missing a field OpenCode's own schema requires) is `kind: "unknown"`: listed and switchable by
  label, never given an email or a usage lookup. An entry whose envelope (`id`, `integrationID`,
  `label`) is malformed is left out: it can be neither shown nor activated.

Accounts sort by integration, then label, then id. `/swap-next` takes the account after the
integration's active one in that order, wrapping around.

## The current session's provider

The footer and `/swap-next` act on the provider the current session uses (`V9`), resolved by
`src/session.ts` in this order:

1. the session's committed model, `data.session.get(id).model.providerID`;
2. the model of the session's last assistant or `model-switched` message;
3. `ui.model.current()`, the prompt's selection (the only source on the home route).

The provider becomes an integration through `data.location.provider.list()`:
`provider.integrationID ?? provider.id` (`V8`). The mapping is never assumed to be the identity
(the OpenCode Console's providers all map to `opencode`), and an unlisted provider resolves to
nothing.

An integration whose active connection is an environment variable (`connections[0].type ===
"env"`, `V8`) shows no footer: the stored label would name a credential OpenCode is not using.

## Usage: revisions, ownership, cache

A credential's value can change with no event: OpenCode refreshes tokens with value-only
updates, provider plugins rewrite inactive credentials at startup, and a `--standalone` client
writes the DB without telling the TUI (`V7`, `V12`). The store therefore keys usage on what the
value is, not on the credential id.

- **Revision tracker.** On every list the store hashes each credential's canonical value
  (SHA-256, Web Crypto) and keeps `credentialID → {digest, revision}`. A changed digest bumps a
  global, non-secret revision counter. The revision key is `credentialID#revision`. The tracker
  is private: digests never reach exposed state.
- **Request ownership.** Each usage request captures its credential's generation and revision
  key and gets an `AbortController`. A switch or update event, a revision change or dispose
  bumps the generation and aborts it. A result is applied only if the store is not disposed and
  both still match, so a late answer for a replaced token or a switched account is dropped.
- **Cache.** Results are cached by revision key and stamped when the request starts. An entry
  is fresh for 55 s: the 60 s poll minus the 5 s list timeout, so every poll finds the previous
  result stale and the footer refreshes on each one. `/swap-refresh` and switch or update events
  clear it (an update event carries no payload, so it clears every integration, `V7`).
- **Back-off.** A 401 or 403 stops lookups for that revision key until `/swap-refresh` or a
  switch or update event. A replaced value is a new key, so it is retried at once.
- **Lists.** Lists are serialized, and each one gives up after 5 s, so a request that never
  settles (a stale client after a reconnect, `V7`) shows as an error instead of blocking the
  plugin.

The footer's first render, and every later change of the session's integration, looks the
active account's usage up at once rather than at the next poll: home and every session render
their own prompt, so navigating mounts a new footer.

## The picker and activation

`/swap` lists, then looks up usage for every eligible account, at most 4 at a time and for at
most 5 s overall (each request also times out after 5 s); the dialog then opens with `…` for
whatever is still pending. Options are grouped by integration (`category`), with the label as
title, the email or key hint as description, and `active` or a usage summary as footer.

Both `/swap` and `/swap-next` end in a confirmation, then `client.credential.activate`:

- a cancelled or dismissed dialog never activates;
- a rejected `activate` shows an error toast and changes nothing displayed. The displayed active
  account only ever changes from a fresh list or the `credential.switched` event, never
  optimistically;
- the session's model is never touched. The success toast notes when the current session uses
  another integration and is unaffected.

`activate` is the plugin's only write. It never creates, updates or removes a credential, never
refreshes a token and never calls a token endpoint: OpenCode refreshes the active credential
lazily on its own requests (`V12`), and a second refresher would race it for single-use refresh
tokens.

## Packaging

OpenCode 2 loads a package's `./tui` export, whose default export must be `{id, setup}`
(`V4/V5`). The published payload is shaped by three facts:

- **`exports` is exactly `{"./tui": "./dist/tui.js"}`.** A `"."` export makes
  `opencode plugin add` register the package as a server plugin (`V4/V5`).
- **The output is precompiled.** OpenCode's runtime Solid transform skips `node_modules`, and a
  raw `.tsx` from npm fails to load (`V4/V5`). `scripts/build.ts` runs `@opentui/solid`'s
  `transformSolidSource` on every file under `src/`, into a mirrored `dist/`, rewriting relative
  imports to `.js`.
- **The peers resolve under OpenCode's installer.** OpenCode installs plugins with Arborist
  9.4.0, `audit: false`, `ignoreScripts: true` and no `legacyPeerDeps` (`V4/V5`); a peer conflict
  Bun only warns about makes the plugin uninstallable.

`check-tui-entry-compiled.sh` and `check-npm-resolution.sh` check the packed tarball for each,
in CI and before every publish.
