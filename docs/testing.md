# Testing

```bash
make plugin-test      # bun test ./test
make verify           # everything CI's verify job runs
```

The unit suite runs in a few seconds with no network, no OpenCode and no credentials. Nothing
in `test/` may start OpenCode, read OpenCode's directories or reach a real endpoint; a test
that needs any of them is a broken test.

## Injected fakes

`AccountStore` (`src/state.ts`) takes every dependency as an argument (`StateDeps`): the
credential client, the event source, `fetchImpl`, the clock, the timers, the dialogs, the toast
and the session status. Tests pass fakes for all of them, so orchestration is tested without a
TUI, without real time and without a network.

- **Time** is `ManualTimers` in `test/state.test.ts`: intervals and timeouts fire only when a
  test advances them, so the 60 s poll, the 55 s cache and the 5 s deadlines are tested exactly.
- **HTTP** is `fakeFetch`, `jsonResponse`, `textResponse` and `hangingFetch` from
  `test/helpers.ts`. `fakeFetch` records every call, so a test asserts what was sent, to which
  URL, and that nothing was sent when nothing should be.
- **Promises** a test needs to settle out of order (a list or a usage request answering after a
  switch) are deferred promises the test resolves itself.
- **Credentials** are fakes: `makeJwt` builds unsigned JWT-shaped tokens with made-up claims,
  and keys are obvious dummies. Tests plant these values and assert they never appear in exposed
  state, `JSON.stringify` of it, toasts, dialog options or error messages.

`test/plugin.test.ts` runs `setup` against a recording context: every slot, layer, toast and
call is captured, so feature detection, the provider chain and the command layer are tested as
OpenCode would call them.

## What's covered

- **Usage lookups** (`usage-openai`, `usage-zai`, `usage-http`): response parsing, ported from
  the 0.4.x CLI's tests; every failure as fixed text; timeouts and aborts; redirects refused; and
  leak tests: a planted token never in a message, including thrown header errors and echoed
  response bodies.
- **Projection** (`accounts`): `AccountView` carries no secret, unknown shapes become
  `kind: "unknown"`, malformed envelopes are left out, sort order, next-account wrap-around,
  usage eligibility, integration resolution and the footer account (env connections).
- **Orchestration** (`state`):
  - late results after a switch, after an update event and after dispose are never applied;
  - the cache: hits for an unchanged value, its 55 s freshness, invalidation by events and
    `/swap-refresh`;
  - a value replaced with no event (a z.ai key, an OAuth access token with the same
    `expires`) bumps the revision on the next poll, lifts the back-off and is fetched afresh;
  - the 401/403 back-off and its lifting;
  - the picker's 4-request limit, its 5 s deadline and per-request aborts;
  - a cancelled select or confirm never activates; a rejected `activate` keeps the previous
    active account;
  - dispose clears the interval, the subscriptions and pending requests.
- **Plugin** (`plugin`): feature detection, the provider chain (session model, messages, prompt
  model), env connections, the command layer and `setup`'s registration and teardown.
- **Presentation** (`bands`, `options`, `jwt`): window labels, pace bands and reset times;
  option parsing; JWT claim decoding.
- **License headers** (`license-headers`): `scripts/check-license-headers.sh` passes on a
  tree where every file has its header and fails on a file without one.

## Packaging checks

`make verify` also packs the plugin and checks the tarball, the way OpenCode will see it:

- `make plugin-entry-check` (`scripts/check-tui-entry-compiled.sh`): exports exactly `./tui`,
  every file precompiled `.js`, no generic JSX runtime, no runtime `effect` or `@opencode/*`
  import, relative imports with `.js`, and an entry whose default export is `{id, setup}`.
- `make plugin-npm-resolve-check` (`scripts/check-npm-resolution.sh`): OpenCode's Arborist
  version resolves the tarball's peers from a clean project.

A change to either script must be checked by breaking what it guards once and seeing it fail.
[`opencode-v2-integration.md`](opencode-v2-integration.md) records the breaks used so far.

## The OpenCode sandbox

Behaviour that needs a real OpenCode (loading, rendering, slash commands, events) is checked by
hand in an isolated install, never in your own:

```bash
make sandbox                                  # OpenCode 2 in /tmp/oc2 (OC_SANDBOX_VERSION=2.0.x)
make sandbox-v1                               # OpenCode 1.19 in /tmp/oc1
scripts/oc2-sandbox.sh run auth list          # any OpenCode command, sandboxed
scripts/oc2-sandbox.sh tui                    # the TUI in a detached tmux session
scripts/oc2-sandbox.sh capture                # print the TUI's screen
scripts/oc2-sandbox.sh keys '/swap' Enter     # type into it
scripts/oc2-sandbox.sh stop                   # stop the TUI and OpenCode's background service
scripts/oc2-sandbox.sh check                  # fail if a real OpenCode directory changed
```

The script runs OpenCode with a cleared environment and `HOME`, `XDG_*` and `TMPDIR` inside the
sandbox, so no real configuration, credential or provider key is seen; its header explains
each command. Use fake credentials only (`opencode auth import --standalone` of JWT-shaped
fakes), and run `check` at the end.

To load a build of the plugin, publish it to a local registry and point the sandbox at it:
`npx verdaccio` in a scratch directory, `npm publish --registry http://127.0.0.1:4873/` of a
prerelease version, `OC_SANDBOX_REGISTRY=http://127.0.0.1:4873/`, then list the package in the
sandbox's `cli.json` `"plugins"`. The end-to-end runs recorded in
[`opencode-v2-integration.md`](opencode-v2-integration.md#plugin-end-to-end) used exactly this.

## Live verification

Real accounts are not part of any automated check. What still needs one is listed under
"Deviations and open items" in [`opencode-v2-integration.md`](opencode-v2-integration.md),
starting with real usage numbers for ChatGPT and z.ai accounts.
