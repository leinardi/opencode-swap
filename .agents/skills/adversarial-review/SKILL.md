---
name: adversarial-review
description: >
  Adversarial code review of changes to opencode-swap, the OpenCode 2 TUI
  plugin: working tree, staged diff, branch, commit range, or PR. Hunts for
  secret leaks, writes beyond credential.activate, late async results applied
  as current, unverified OpenCode API assumptions, broken packaging, release
  workflow drift, and test-isolation violations, then reports ranked findings.
  Use when the user asks to review changes, a diff, PR, branch, or commit;
  check work before committing; assess merge readiness; or poke holes in an
  implementation.
---

# Adversarial Review - opencode-swap

Assume the change is wrong until proven right: it hides a bug, breaks an
invariant, leaks a credential, or drifts from a contract. Find the concrete
input, state, failure point, or interleaving where it fails. Do not praise or
restyle the change. A review with no findings is credible only after active
attempts to break the changed behavior.

This skill defines the review procedure and reporting. `AGENTS.md` and the
project documentation remain the sources of truth.

Copy this checklist and tick items as you go:

```text
Review progress:
- [ ] 1. Diff and intent established (default scope if none given)
- [ ] 2. AGENTS.md and the docs for each changed area read
- [ ] 3. Repository invariants checked
- [ ] 4. Adversarial passes run
- [ ] 5. Findings confirmed or dropped; gates run
- [ ] 6. Report written
```

## 1. Establish the diff

Never review from memory or only from the user's description. Read the actual
diff and determine its intent. With no scope given, review the uncommitted
work; if the tree is clean, review the branch against `main`.

| User intent | Command |
| --- | --- |
| "my work", "before I commit", uncommitted changes | `git status --short`, then `git diff HEAD`; inspect untracked files too |
| staged changes only | `git diff --staged` |
| branch, "this PR", "ready to merge" | `git diff main...HEAD` |
| specific commit range | `git diff <base>..<head>` |
| GitHub PR number | `gh pr view <n>` for intent and metadata, then `gh pr diff <n>` |

Read `git log --oneline` for the reviewed range and any linked issue or PR
body. Code that works but does something other than the stated intent is a
finding.

Read every changed file with enough surrounding context to understand its
contracts. For non-trivial behavior changes, inspect callers, implementations,
tests, and documentation that depend on the changed symbol. Find call sites
with Serena's `mcp__serena__find_referencing_symbols` when it is available,
otherwise with `grep -rn '<symbol>' src/ test/`; never assume every caller
is in the diff.

A change on `maint/0.4` is the 0.4.x Python CLI and OpenCode 1 plugin: review
it against that branch's own `AGENTS.md` and this skill as it exists there,
not this one.

## 2. Load project authority

Always read `AGENTS.md`. Load only the additional documentation relevant to
the changed paths:

| Changed area | Read | Review focus |
| --- | --- | --- |
| any use of the plugin context (`client`, `data`, `ui`, `keymap`, `theme`) | `docs/opencode-v2-integration.md` | the behavior is the one recorded there, with source or sandbox evidence; a new member is in `missingFeatures` and exists in 2.0.20 |
| `src/state.ts` | `docs/architecture.md` (usage, picker and activation sections) | request ownership, revision keys, cache and back-off timing, dispose |
| `src/accounts.ts`, `src/jwt.ts`, `src/usage/**` | `docs/security.md`, `docs/provider-support.md` | the secret boundary, fixed-text failures, endpoints and eligibility |
| `src/footer.tsx`, `src/commands.ts`, `src/tui.tsx`, `src/session.ts` | `docs/architecture.md`, `README.md` | the documented commands, footer states and provider chain |
| `package.json`, `scripts/build.ts`, the check scripts | `docs/architecture.md` (packaging) | `./tui`-only precompiled payload, optional peers, the guards still failing on what they guard |
| tests or test infrastructure | `docs/testing.md` | injected fakes, no real OpenCode or network, orchestration coverage |
| releases, `package.json` version, workflows | `docs/releasing.md`, `CONTRIBUTING.md` | re-runnable release workflow, dist-tags, trusted publishing |
| user-visible behavior | `README.md` | commands, options, network access and requirements match the code |

No matching document does not mean lighter review. Apply `AGENTS.md`, the
cross-cutting invariants below, and the general adversarial passes.

## 3. Repository invariants

`AGENTS.md` "Core invariants" and "Testing rules" are the checklist: check
every line whenever the change affects it, directly or indirectly. A
credential value reaching UI state, a toast, a thrown message or a log; any
credential write but `activate`; or a usage request to a host other than the
account's own endpoint is a blocker. In addition:

- **`AccountView` is the only shape in exposed state.** It is built field by
  field; a spread of a credential entry or its value, or a new field derived
  from a secret beyond the email and the 4-character key hint (keys of 20+
  characters only), is a finding.
- **A secret lives only as long as its request.** `usageRequest` reads it from
  the live entry; anything that keeps it (a cache, a closure retained past the
  request, a map keyed or valued by it) is a finding. The revision tracker
  keeps digests, never values, and never exposes them.
- **The active account changes only from a list or `credential.switched`.**
  An optimistic update after `activate`, or an update from any other source,
  is a finding.
- **Every OpenCode assumption is recorded.** Behavior the change relies on
  must be in `docs/opencode-v2-integration.md` with a source citation or
  sandbox evidence. A new context member missing from `missingFeatures`, or
  absent from the 2.0.20 types, is a finding; so is a claim in docs or
  comments that contradicts that file.
- **The payload stays loadable.** `exports` exactly `./tui`; every `dist/`
  file compiled by `scripts/build.ts`; no runtime `effect` or `@opencode/*`;
  peers optional and resolvable by Arborist 9.4.0.
- **Releases stay reconcilable.** `docs/releasing.md` is the contract.
  `auto-tag-release.yaml` must finish on a re-run: create a tag only if
  missing, fail (never move) if it marks another commit, create a release
  only if missing, and dispatch the publisher only while npm lacks the
  version; a commit whose parent has no root `package.json` is never a
  release. The publisher publishes a version below `latest` under `legacy`.
  An explicit release version must exceed the latest `v*` tag. Publishing
  stays on trusted publishing (OIDC); a stored `NPM_TOKEN` is a finding.
- **Workflows stay pinned and least-privilege.** `contents: read` at the top,
  extra permissions per job with the reason. Every action is pinned to a full
  commit SHA with a `# vX.Y.Z` comment; workflow inputs reach shell through
  `env:`, never `${{ }}` inside `run:`. The `conventional-commits` job must
  keep running on `workflow_dispatch`: the release bump PR depends on it.
  `ci-result` is the aggregate check: it needs every gating job, and only
  `conventional-commits` may be skipped, on a push to `main`.

## 4. Adversarial passes

Do not skim for style. Run each pass with "how can this fail?" framing:

- **Credential shapes:** each credential type (`oauth`, `key`, `external`),
  a known type missing a field, an unknown type, a malformed envelope, a JWT
  without the email claim or with a non-JSON payload, a key shorter than 20
  characters, an integration with no stored credential, an env connection.
- **Interleavings:** a list or usage answer arriving after a switch, an
  update event, a value change with no event, a newer list, dispose; two
  lists racing; a list that never settles; the picker deadline expiring with
  requests in flight; a dialog resolved after dispose; `activate` rejecting.
  Confirm what exposed state shows after each.
- **Timing:** the 60 s poll against the 55 s cache, the 5 s list and request
  timeouts, the 401/403 back-off and what lifts it, the footer's first lookup
  on mount and on a change of integration.
- **Provider resolution:** a session with no committed model, home route,
  messages not yet loaded, a provider whose `integrationID` differs from its
  id, an unlisted provider.
- **Secret exposure:** inspect toasts, dialog options, footer text, thrown and
  rejected errors, `JSON.stringify` of state, test diagnostics, and anything
  that formats an exception, a header or a response body. Search for a
  credential value crossing into a string the user or a log can see.
- **OpenCode compatibility:** a context member absent on 2.0.20, a behavior
  that differs between 2.0.20 and the latest 2.0.x, a keymap layer outside
  `mode: "global"` or outside the `app` slot render, a slot or dialog field
  newer than the minimum.
- **Contract drift:** compare implementation with README (commands, footer
  states, options, network access, requirements), the docs and all callers.
  Flag undocumented behavior, option or endpoint changes.
- **Tests:** require behavior-focused regression coverage for changed
  behavior. Reject tests that pass against old code, assert mocks instead of
  exposed state, weaken existing assertions, use real timers or a real
  network, or plant no secret where a value is handled.
- **Commit type matches whether it ships:** with no explicit version,
  commitizen derives the release from the commit types since the last `v*`
  tag (`CONTRIBUTING.md#versioning`): `feat` a minor; `fix`, `perf` and
  `refactor` a patch; `!` or a `BREAKING CHANGE:` footer a major; everything
  else nothing. Pull requests land as merge commits, so every commit counts.
  What ships is the plugin on npm: `src/**`, `scripts/build.ts`, and
  `package.json`'s runtime fields (`exports`, `files`, `engines`, peers). A
  behavior change there typed `build`, `chore`, `ci`, `docs`, `style` or
  `test` never releases on its own: a finding. So is a `feat` or `fix`, or a
  `perf` or `refactor` (both bump here), on a change users do not receive
  (CI, docs, tests, tooling), which cuts a release with nothing in it; a
  stray `!` or `BREAKING CHANGE:` footer; and a `revert` of a shipped
  `feat`/`fix` that is not typed `fix` (a `revert` bumps nothing). commitizen
  matches every line of every message, merge commits included, against
  `^<word>(<scope>)?!?:` and bumps when the word starts with `feat`, `fix`,
  `refactor` or `perf`: a body line such as `fixtures: ...`, or a pull
  request title typed that way (GitHub writes it into the merge commit's
  body), counts too. A comment-only change to a shipped file (a licence
  header, a typo) may be `docs` or `chore`; it ships with the next release.
- **A meta-test that can no longer fail:** `scripts/check-license-headers.sh`
  (with `test/license-headers.test.ts`), `scripts/check-npm-resolution.sh`,
  `scripts/check-tui-entry-compiled.sh` and the `ci-result` job guard claims
  nothing else checks. Break the guarded thing once (strip a header, point
  the entry at raw `.tsx`, add a runtime `effect` import, make a needed job
  fail) and see whether the check still fails. A check that passes because
  its file list is empty, its pattern no longer matches anything, its loop
  never ran, or a gating job is missing from `ci-result`'s `needs` is a false
  pass: a finding.

For each candidate finding, reproduce it or trace the failing input end to
end. If that confirms it, report it. If not, dig once more; if it is still
unconfirmed, drop it. Never report a finding without the triggering state and
the wrong result or broken invariant.

## 5. Verify findings and gates

Use focused tests while investigating, then run the gate the changed set owes
(`AGENTS.md` "Validation matrix"). Never let verification start the real
OpenCode, read its directories, or use a real credential; behavior that needs
OpenCode is checked only in `scripts/oc2-sandbox.sh` with fake credentials.

| Diff touched | Run |
| --- | --- |
| `src/**`, `test/**`, `package.json`, `bun.lock`, build or lint config | `make plugin-typecheck plugin-lint plugin-test plugin-package-check plugin-entry-check plugin-npm-resolve-check` |
| docs, skills, YAML or shell | `make check` |
| `scripts/check-license-headers.sh`, `test/license-headers.test.ts` | `make plugin-test`, `make check`, and the mutation from the meta-test pass |
| `scripts/check-npm-resolution.sh`, `scripts/check-tui-entry-compiled.sh` | `make plugin-npm-resolve-check plugin-entry-check`, and the mutation from the meta-test pass |
| `.github/workflows/**` | `pre-commit run actionlint --all-files`, `make check` |
| broad change or merge-readiness review | `make verify` |

The full test suite is fast and isolated, so source changes normally warrant
the full suite rather than only targeted tests. A failing gate is a confirmed
finding when caused by the reviewed change. If a gate cannot run, state why and
mark it unverified; never imply it passed.

## 6. Report

Rank findings by severity, worst first. A credential leak, a credential write
beyond `activate`, a request to another host, or a stale result shown as
current are normally blockers. Skip pure formatting unless it changes meaning
or breaks a required gate.

For each finding:

```text
<path>:<line> - <severity: blocker | high | medium | low>: <one-line defect>
  Failure: <concrete input/state/interleaving -> wrong result or broken invariant>
  Fix: <specific corrective change>
```

Put findings first. Then list open questions or assumptions, followed by a
one-line verdict: **block**, **approve with nits**, or **approve**. Include gates
actually run and gates not run. If no findings exist, say so explicitly and
briefly name the failure modes you tried to trigger. Be blunt, but never invent
a finding to appear thorough.
