# Provider Research Prompt

Use this prompt to collect source-backed evidence needed to add or verify an
OpenCode provider in opencode-swap.

## Before You Start

1. Update OpenCode to version you want investigated.
2. Start OpenCode in a non-sensitive project directory.
3. Log into the target provider through OpenCode's normal `auth login` or
   provider-login flow **before** pasting the
   prompt. Do not paste, export, or otherwise reveal any credential.
4. Use `smoke` mode unless one normal provider request is not authorized.
   `structure` mode is fallback for quota, billing, or policy constraints.

`smoke` examines a sanitized in-memory provider record, makes one normal
provider request, and compares sanitized before/after state. It can consume
provider quota and may cause OpenCode to refresh an expired credential. Do not
submit a source-only report: maintainers can perform that research themselves.

Never run this against an account whose usage, billing, or data access you are
not authorized to exercise. The prompt does not need `opencode-swap` installed.

## Copy-Paste Prompt

Replace `<TARGET_PROVIDER>` with provider's common name, ID, or URL. Replace
`<LIVE_MODE>` with exactly `smoke` or `structure`; use `smoke` unless a request
is prohibited. Paste the
whole block into OpenCode. Return report in chat, then open a dedicated issue
in this project's GitHub tracker titled `Provider research: <TARGET_PROVIDER>`
and paste the report unchanged.

```text
Research OpenCode support for provider <TARGET_PROVIDER> so opencode-swap can
safely add or verify account switching. Produce one sanitized Markdown report
for a public GitHub issue. Do not modify source code, configuration, auth
state, or provider accounts.

Live mode: <LIVE_MODE>
- structure: source analysis plus sanitized in-memory inspection of only target
  provider's existing auth record. Do not make provider requests.
- smoke: structure mode plus at most one normal minimal provider request using
  currently configured target provider. This is authorized only because this
  mode is explicitly selected. It may consume quota and trigger token refresh.
  Do not initiate login, refresh, logout, or account changes yourself.

Security boundary, mandatory:
- Never print, copy, hash, summarize, or attach raw auth.json or any
  credential file.
- Never read or print environment variables, shell history, keychains, secret
  stores, browser profiles, process arguments, OpenCode logs, request traces,
  or debug output.
- Never request a token, API key, refresh token, access token, cookie, JWT,
  authorization header, account ID, email, organization ID, endpoint secret,
  or auth-file content.
- Do not run commands that print raw credential/config/log/keychain content,
  including cat, raw jq output, grep output, or find output against secret
  locations.
- You may inspect local auth state only when necessary and only through an
  in-memory sanitizer whose output is restricted to: target provider key,
  record type, field names, JSON value types, whether a field is present, and
  coarse value-length buckets. The sanitizer must never print a value, a hash,
  a prefix/suffix, a decoded JWT claim value, an email, an account identifier, or a
  URL query parameter. Do not write raw data or intermediate sanitized
  structures to disk.
- In `structure` or `smoke` mode, inspect only exact target provider key
  established from source or explicit user input. Never enumerate or report
  other auth.json keys.
- If source establishes target access token is JWT-shaped, an in-memory
  sanitizer may inspect only source-relevant identity-claim *names*, JSON
  types, presence, and equality across two snapshots. Never output claim
  values, decoded payload, token segments, or a hash. If source does not name
  candidate claims, do not inspect arbitrary claims.
- Do not enable debug logging or intercept traffic. Make a provider request
  only in explicitly selected `smoke` mode. Report only success/failure and
  non-secret error category; never include raw output.
- If any tool output unexpectedly contains a credential-like value, stop and
  omit it from the report. Use only the literal marker <redacted> if needed.

Research process:
1. State selected live mode and only "target record observed" or "target record
   not observed" after permitted sanitizer runs. Continue source analysis if
   target record is absent, but report that live evidence is incomplete.
2. Record OpenCode version, installation channel, operating system, and whether
   provider is built-in, configured, or supplied by an external plugin. Include
   external plugin name and resolved version only when available from package
   metadata or source manifests.
3. Locate local OpenCode source or installed package source. Cite exact relative
   file paths, function/class names, and line ranges for all claims. If source
   is unavailable, say so; do not infer behavior from generic OAuth knowledge.
4. Determine provider's exact auth.json top-level key or keys, auth record type
   (api, oauth, wellknown), required and optional field *names and types*, and
   any metadata field names. Prefer source. If source is insufficient and a
   live login is present, inspect only a sanitized in-memory structural view as
   allowed above. Never report field values. Distinguish clearly between:
   record types accepted by generic Auth schema; record types emitted by this
   provider's login UI; and record types actually consumed by its runtime
   loader. A type not emitted by one login method is not unsupported solely for
   that reason.
5. Determine lifecycle behavior from source: token/API-key refresh, rotation,
   expiry interpretation and units, write-back to auth.json, mutable metadata,
   and behavior after authentication failure.
6. Determine safe account identity evidence. Classify each candidate as:
   stable non-secret provider/user identity; source-derived identity that can
   be absent; or secret-derived identity (refresh/access/API key). Explain
   rotation behavior. A secret-derived identity can still support transient
   in-memory comparison or deduplication, but must never enter registry
   metadata or public output. Do not declare it unsafe solely because it is a
   secret; explain exact ambiguity or rotation risk instead.
7. Determine whether switching one provider key preserves unrelated auth.json
   keys, and list provider-specific request/header/base-URL behavior that makes
   moving a record to a different key unsafe.
8. In `structure` or `smoke` mode with a target record observed, produce a
   sanitized structure observation: provider key, record type, field names,
   JSON types, field presence, coarse string-length buckets, and numeric expiry
   classification (integer/float, non-negative/negative, expired/not expired
   only when source establishes units). Never include values. In `smoke` mode,
   take one such in-memory snapshot before and after the one authorized normal
   request. Sanitizer may retain raw values and numeric expiry in process memory
   only long enough to compare snapshots, then discard them. Report only field
   names added/removed/changed, whether each field is equal/changed, whether
   expiry moved forward/backward/unchanged, and source-relevant identity-claim
   equality result when permitted above. If normal request does not refresh,
   report "refresh not observed" rather than forcing expiry or refresh. Do not
   initiate login, refresh, logout, or account changes.
9. Finish with recommendation: safe to support now, API-only support, guarded
   OAuth support, or defer. Separate source facts, observed behavior, and
   inference. List only minimal opencode-swap provider behavior and regression
   tests that this evidence supports. Describe cross-process refresh races as
   risks; do not require unsupported mechanisms such as compare-and-swap or a
   cooperative OpenCode lock. If opencode-swap source is unavailable, frame
   recommendations as candidate provider requirements; do not prescribe or
   claim an existing opencode-swap behavior.
10. After the final credential scan, write only the final sanitized report to
    `provider-research-<safe-provider-slug>.md` in the current directory.
    `<safe-provider-slug>` must contain only lowercase ASCII letters, digits,
    and hyphens. Do not overwrite an existing file: append `-2`, `-3`, and so
    on until an unused filename exists. Do not write raw auth data, intermediate
    sanitizer output, logs, or any other investigation artifact to disk.

Report format:
# Provider Research: <TARGET_PROVIDER>
## Environment
## Authentication Source
## Auth Record Contract
## Credential Lifecycle
## Stable Identity Assessment
## Switching Constraints
## Live Structure Observation
## Live Smoke Observation
## Recommendation
## Required Tests
## Security Confirmation

Label every material statement as `source`, `observed`, or `inferred` in its
section. In Switching Constraints, say unrelated JSON values must be preserved
with semantic equality; do not require byte-for-byte file preservation because
safe atomic JSON publication can change whitespace and key formatting. Do not
recommend public display/redaction policy or claim a project feature is absent
without examining the project source provided in this session. In Required
Tests, distinguish tests proving OpenCode source contract from candidate
opencode-swap tests; do not require a provider record type to be rejected only
because a specific login method does not emit it.

Security Confirmation must explicitly say whether authentication state was not
inspected, or was inspected only through required in-memory structural
sanitizer and selected live mode. It must also say no raw credential values,
environment variables, keychains, logs, request traces, or credential values
were included. Before returning, scan your report and remove anything
resembling a token, key, JWT, email address, account ID, organization ID, or
URL query parameter. Do not include any value that was not present in public
source.

In `smoke` mode, do not claim authentication state was unchanged: normal
OpenCode request may refresh and persist credentials. State only whether the
sanitized before/after comparison observed a change, and that no intentional
auth mutation was made by this investigation.

After writing the file, reply with its relative path and the same final report
in chat. Do not mention or display intermediate investigation output.
```

## Issue Checklist

- Use one issue per provider and authentication method when behavior differs.
- State selected live mode and whether target provider record was observed,
  without identifying account.
- Paste only report produced by prompt; do not attach auth files, logs, or
  screenshots containing provider/account details.
- Review report once more before posting. Remove any accidental personal or
  credential-like value.
- Link relevant public OpenCode source or package versions when report provides
  them.
