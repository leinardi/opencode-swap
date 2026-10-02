---
name: adversarial-review
description: >
  Adversarial code review of changes to opencode-swap: working tree, staged
  diff, branch, commit range, or PR. Hunts for credential loss, secret leaks,
  unsafe filesystem writes, schema-compatibility bugs, transaction failures,
  concurrency races, and test-isolation violations, then reports ranked
  findings. Covers the Python CLI and the OpenCode TUI plugin. Use when the
  user asks to review changes, a diff, PR, branch, or commit; check work
  before committing; assess merge readiness; or poke holes in an
  implementation.
---

# Adversarial Review - opencode-swap

Assume the change is wrong until proven right: it hides a bug, breaks an
invariant, loses a credential, or drifts from a contract. Find the concrete
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
otherwise with `grep -rn '<symbol>' src/ tests/`; never assume every caller
is in the diff.

## 2. Load project authority

Always read `AGENTS.md`. Load only the additional documentation relevant to
the changed paths:

| Changed area | Read | Review focus |
| --- | --- | --- |
| auth schema, provider parsing, JWT claims | `docs/opencode-auth.md`, `docs/architecture.md` | exact OpenCode behavior, strict schema validation, provider seam |
| switching, backups, restore, locking, atomic writes | `docs/architecture.md` | sync-back, transaction boundary, rollback, atomicity, races |
| secret store, Keychain/files, permissions | `docs/security.md`, `docs/architecture.md` | backend routing, secret boundary, modes, fallback behavior |
| tests or test infrastructure | `docs/testing.md` | real-resource isolation, failure injection, coverage expectations |
| CLI commands or user-visible behavior | `README.md` and relevant source docs | command contract, prompts, output secrecy, documented behavior |
| `integrations/opencode-tui-plugin/**` | `docs/architecture.md` (module map), the plugin `README.md` | the plugin reads only `opencode-swap status --json` and invokes existing CLI operations, never credential storage or `auth.json`; `status --json` schema-version pinning; the documented network access |
| `status --json` output | the plugin `README.md`, `integrations/opencode-tui-plugin/src/tui.tsx` | schema compatibility with the TUI plugin |
| releases, version files, workflows | `docs/releasing.md`, `CONTRIBUTING.md` | lockstep versions, re-runnable release workflow |
| roadmap or scope claims | `docs/roadmap.md` | intentional omissions versus accidental incompleteness |

No matching document does not mean lighter review. Apply `AGENTS.md`, the
cross-cutting invariants below, and the general adversarial passes.

## 3. Repository invariants

`AGENTS.md` "Core invariants" and "Testing rules" are the checklist: check
every line whenever the change affects it, directly or indirectly. A direct
truncating write, an overwrite of a live credential before it is preserved, or
a `SchemaError` caught through a base class is a blocker. In addition:

- **Rollback restores live truth.** If `auth.json` changed and later
  bookkeeping fails, rollback must restore exact pre-switch content. Failures
  before the live atomic replace must not disturb live state.
- **Other provider values remain unchanged.** A provider splice may replace
  only the owned provider entry; unrelated entries must survive
  add/use/restore with exact value equality.
- **Filesystem permissions remain strict.** Secret, registry, backup, and temp
  files are `0600`; private directories are `0700`. Check both creation and
  pre-existing-path cases.
- **Registry identity never caches a refresh-token fallback.** The only
  credential-derived value allowed outside secret storage is the documented
  last-4 key hint (`providers.common.key_account_hint`); anything longer or
  reconstructible is a finding.
- **Secret-store routing follows `docs/security.md`.** Sticky fallback, which
  copy wins on read, and reconcile-on-write are specified there; a change that
  silently migrates or strands a credential is a finding.
- **Provider seam stays minimal.** Provider-specific record interpretation
  belongs in `providers/`; generic whole-file I/O belongs in
  `opencode_auth.py`; orchestration and lock ownership belong in `switcher.py`;
  secret persistence belongs in `store.py`.
- **The TUI plugin stays a presentation layer.** It reads only
  `opencode-swap status --json` and calls existing CLI operations; it never
  reads credential storage or `auth.json`, so it cannot bypass the switch
  transaction. `status --json` follows the `schema_version` contract in
  `cli.py` (`_status_payload`): a removed, renamed or repurposed field bumps
  the version and needs a matching plugin change; the plugin must tolerate
  unknown fields and `state` values.
- **Releases stay reconcilable and in lockstep.** `docs/releasing.md` is the
  contract. The CLI and the TUI plugin share one version across
  `pyproject.toml`, `__init__.py` and `package.json`. `auto-tag-release.yaml`
  must finish on a re-run: create a tag only if missing, fail (never move) if
  it marks another commit, create a release only if missing, and dispatch a
  publisher only while its registry lacks the version. An explicit release
  version must exceed the latest `v*` tag. Publishing stays on trusted
  publishing (OIDC); a stored `PYPI_API_TOKEN`/`NPM_TOKEN` is a finding.
- **Workflows stay pinned and least-privilege.** `contents: read` at the top,
  extra permissions per job with the reason. Every action is pinned to a full
  commit SHA with a `# vX.Y.Z` comment; workflow inputs reach shell through
  `env:`, never `${{ }}` inside `run:`. The `conventional-commits` job must
  keep running on `workflow_dispatch`: the release bump PR depends on it.

## 4. Adversarial passes

Do not skim for style. Run each pass with "how can this fail?" framing:

- **Credential lifecycle:** expired/missing access token, rotated refresh token,
  absent `accountId`, refresh-token identity fallback, managed versus foreign
  live login, already-active target, missing secret-store record, malformed or
  absent `auth.json`.
- **Transaction failure points:** fail before and after each durable write,
  especially sync-back, pristine/bak/unclaimed backup, live atomic replace,
  registry update, and rollback. Confirm exact live and stored state after each.
- **Schema and exceptions:** malformed top-level JSON, wrong record type,
  missing/extra fields, broad `except`, wrong sentinel, errors converted to
  "not found", and cleanup exceptions masking the original failure.
- **Filesystem boundaries:** missing parent, existing paths with permissive
  modes, temp-file cleanup, same-directory rename, path/environment overrides,
  partial/corrupt files, and write errors. Look for TOCTOU assumptions.
- **Concurrency:** two switchers targeting different accounts, lock timeout,
  lock scope that ends before durable state is coherent, and unavoidable
  OpenCode refresh races. Do not confuse thread tests with cooperation from
  OpenCode.
- **Secret exposure:** inspect formatted output, exceptions, subprocess args,
  dataclass representations, JSON serialization, registry metadata, test
  diagnostics, and backup naming. Search for token-shaped fields crossing a
  non-secret boundary; the last-4 key hint is the only allowed exception.
- **Python correctness:** shallow copies of nested auth data, truthiness
  conflating missing and empty values, timestamp units, and subprocess return
  codes.
- **Contract drift:** compare implementation with README, CLI help, architecture,
  OpenCode behavior, commit/PR intent, and all callers. Flag undocumented command,
  path, schema, exit-code, or storage changes.
- **Tests:** require behavior-focused regression coverage for changed behavior.
  Reject tests that pass against old code, assert mocks instead of durable state,
  weaken existing assertions, use uppercase account names while expecting
  non-normalized keys, or inject failure at a broader/wrong write call.

For each candidate finding, reproduce it or trace the failing input end to
end. If that confirms it, report it. If not, dig once more; if it is still
unconfirmed, drop it. Never report a finding without the triggering state and
the wrong result or broken invariant.

## 5. Verify findings and gates

Use focused tests while investigating, then run the gate the changed set owes.
Never allow verification to access real keychains or live OpenCode data.

| Diff touched | Run |
| --- | --- |
| Python source or tests | `uv run pytest -q` |
| packaging, dependencies, entry points, or build configuration | `uv run pytest -q`, then `uv build` |
| TUI plugin | `make tui-plugin-typecheck tui-plugin-lint tui-plugin-package-check tui-plugin-entry-check` |
| docs, skills, YAML or shell | `make check` |
| broad change or merge-readiness review | `make verify` |

The full test suite is fast and isolated, so source changes normally warrant
the full suite rather than only targeted tests. A failing gate is a confirmed
finding when caused by the reviewed change. If a gate cannot run, state why and
mark it unverified; never imply it passed.

## 6. Report

Rank findings by severity, worst first. Credential loss, secret disclosure,
live-state corruption, or bypassed atomicity are normally blockers. Skip pure
formatting unless it changes meaning or breaks a required gate.

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
