---
name: adversarial-review
description: >
  Adversarial code review of changes to opencode-swap: working tree, staged
  diff, branch, commit range, or PR. Hunts for credential loss, secret leaks,
  unsafe filesystem writes, schema-compatibility bugs, transaction failures,
  concurrency races, and test-isolation violations, then reports ranked
  findings. Use whenever the user asks to review changes, a diff, PR, branch,
  or commit; check work before committing; assess merge readiness; or poke
  holes in an implementation.
---

# Adversarial Review - opencode-swap

Assume the change is wrong until proven right: it hides a bug, breaks an
invariant, loses a credential, or drifts from a contract. Find the concrete
input, state, failure point, or interleaving where it fails. Do not praise or
restyle the change. A review with no findings is credible only after active
attempts to break the changed behavior.

This skill is the entry point for reviewing any change in this repository.
`AGENTS.md` and the project documentation remain the sources of truth; this
skill defines review procedure and reporting.

## 1. Establish the diff

Never review from memory or only from the user's description. Read the actual
diff and determine its intent.

| User intent | Command |
| --- | --- |
| "my work", "before I commit", uncommitted changes | `git status --short`, then `git diff HEAD`; inspect untracked files too |
| staged changes only | `git diff --staged` |
| branch, "this PR", "ready to merge" | determine the default/base branch, then `git diff <base>...HEAD` |
| specific commit range | `git diff <base>..<head>` |
| GitHub PR number | `gh pr view <n>` for intent and metadata, then `gh pr diff <n>` |

Read `git log --oneline` for the reviewed range and any linked issue or PR
body. Code that works but does something other than the stated intent is a
finding.

Read every changed file with enough surrounding context to understand its
contracts. For non-trivial behavior changes, inspect callers, implementations,
tests, and documentation that depend on the changed symbol. Use symbol and
reference tools rather than assuming all call sites appear in the diff.

## 2. Load project authority

Always read `AGENTS.md`. Load only the additional documentation relevant to
the changed paths:

| Changed area | Read | Review focus |
| --- | --- | --- |
| auth schema, provider parsing, JWT claims | `docs/opencode-auth.md`, `docs/architecture.md` | exact OpenCode behavior, strict schema validation, provider seam |
| switching, backups, restore, locking, atomic writes | `docs/architecture.md` | sync-back, transaction boundary, rollback, atomicity, races |
| secret store, keychain/keyring, permissions | `docs/security.md`, `docs/architecture.md` | backend routing, secret boundary, modes, fallback behavior |
| tests or test infrastructure | `docs/testing.md` | real-resource isolation, failure injection, coverage expectations |
| CLI commands or user-visible behavior | `README.md` and relevant source docs | command contract, prompts, output secrecy, documented behavior |
| roadmap or scope claims | `docs/roadmap.md` | intentional omissions versus accidental incompleteness |

No matching document does not mean lighter review. Apply `AGENTS.md`, the
cross-cutting invariants below, and the general adversarial passes.

## 3. Repository invariants

Check these whenever affected, directly or indirectly:

- **All managed-state writes are atomic.** OpenCode's `auth.json` and every
  file under opencode-swap's data directory must use the shared atomic-write
  path: same-directory temp file, `0600`, then `os.replace`. Direct truncating
  writes are blocker-level defects.
- **Never overwrite a live credential before preserving it.** Every switch
  must first sync a managed account's rotated record back to secret storage or
  stash an unmanaged record as an unclaimed backup. Trace every overwrite path,
  including errors and self-switches.
- **Unknown schemas fail loudly.** Never drop fields, coerce an unrecognized
  provider record, or catch `SchemaError` through a broad base exception and
  reinterpret it as absence. Compatibility uncertainty must leave live state
  untouched.
- **Secrets stay in secret storage.** Tokens and API keys must never enter
  `registry.json`, `AccountMeta`, CLI output, logs, exception messages, command
  arguments, or generated artifacts. Registry identity must not cache a refresh
  token fallback.
- **No custom cryptography.** Preserve OS keychain/keyring-first routing and
  sticky `0600` obfuscated-file fallback. File fallback wins reads and is
  reconciled on writes; do not silently migrate or strand credentials.
- **Mutations hold `Switcher.lock`.** Every mutating `Switcher` operation must
  hold the lock across its complete transaction. This lock coordinates only
  opencode-swap processes; never claim it eliminates races with OpenCode.
- **Rollback restores live truth.** If `auth.json` changed and later bookkeeping
  fails, rollback must restore exact pre-switch content. Failures before the
  live atomic replace must not disturb live state.
- **Other provider values remain unchanged.** OpenAI splice operations may
  replace only the owned provider entry; unrelated entries must survive
  add/use/restore behavior with exact value equality.
- **Filesystem permissions remain strict.** Secret, registry, backup, and temp
  files are `0600`; private directories are `0700`. Check both creation and
  pre-existing-path cases.
- **Tests never touch real credentials or auth state.** Tests use
  `Platform.UNKNOWN` or mocked backend functions and point OpenCode paths at
  `tmp_path`/isolated `XDG_DATA_HOME`. No network, prompts, real keychain, or
  real `~/.local/share/opencode/auth.json`.
- **Provider seam stays minimal.** Provider-specific record interpretation
  belongs in `providers/`; generic whole-file I/O belongs in
  `opencode_auth.py`; orchestration and lock ownership belong in `switcher.py`;
  secret persistence belongs in `store.py`.

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
  non-secret boundary.
- **Python correctness:** mutable aliasing, shallow copies of nested auth data,
  truthiness conflating missing and empty values, `Path` edge cases, enum/string
  mismatch, timestamp units, overly broad exception handling, subprocess return
  codes, and platform-specific behavior.
- **Contract drift:** compare implementation with README, CLI help, architecture,
  OpenCode behavior, commit/PR intent, and all callers. Flag undocumented command,
  path, schema, exit-code, or storage changes.
- **Tests:** require behavior-focused regression coverage for changed behavior.
  Reject tests that pass against old code, assert mocks instead of durable state,
  weaken existing assertions, use uppercase account names while expecting
  non-normalized keys, or inject failure at a broader/wrong write call.

Prefer one reproducible defect over ten vague suggestions. If you cannot name
the triggering state and wrong result or broken invariant, keep investigating
or omit it.

## 5. Verify findings and gates

Use focused tests while investigating, then run the gate the changed set owes.
Never allow verification to access real keychains or live OpenCode data.

| Diff touched | Run |
| --- | --- |
| Python source or tests | `uv run pytest -q` |
| packaging, dependencies, entry points, or build configuration | `uv run pytest -q`, then `uv build` |
| broad change or merge-readiness review | `make verify` |
| docs or skill only | inspect rendered content/references and run `git diff --check` |

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
