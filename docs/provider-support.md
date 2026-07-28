# Provider support

## Verification status

**OpenAI is the only provider tested end to end with real accounts.** Every
other entry below is implemented from OpenCode source, pinned plugin source,
and synthetic automated tests. Maintainer does not have accounts for those
services. Testers are wanted for every non-OpenAI provider; report provider,
auth method, OpenCode version, and sanitized observations without credentials.
Use [`provider-research-prompt.md`](provider-research-prompt.md) to generate a
safe, source-backed report for a dedicated GitHub issue.

OpenCode checkout analyzed at commit
`7534d23551f665e65080809975b4ca5c7d63807b` (2026-07-25). External built-ins
were inspected at versions pinned by that checkout:

- `opencode-gitlab-auth@2.1.0`
- `opencode-poe-auth@0.0.1`

## Why records need provider-specific handling

OpenCode stores only three top-level record types: `api`, `oauth`, and
`wellknown`. Provider ID is outer `auth.json` key. Static API records use a
common runtime path, but OAuth providers assign different meaning to expiry,
refresh, and account fields. opencode-swap accepts arbitrary canonical API
records while requiring explicit implementations for OAuth.

| Provider/auth | Status | OpenCode behavior relevant to switching |
| --- | --- | --- |
| OpenAI API/OAuth | ✅ Supported; end-to-end tested | OAuth rotates tokens and usually has stable `accountId` |
| Arbitrary canonical API provider | ⚠️ Supported; testers wanted | Static `key`; optional string-to-string metadata |
| GitHub Copilot OAuth | ⚠️ Supported; testers wanted | Durable token in `refresh` and `access`; `expires=0` means usable |
| Poe API/OAuth | ⚠️ Supported; testers wanted | Browser flow yields API key; no refresh rotation |
| xAI API | ⚠️ Supported; testers wanted | Canonical API record |
| xAI OAuth | ⚠️ Guarded support; testers wanted | Rotates tokens; accepted only when access JWT has stable `iss` and `sub` |
| DigitalOcean | ⚠️ Generic API support; testers wanted | Browser OAuth result is stored as API record; metadata may change |
| Azure/Cloudflare/Snowflake PAT/GitLab PAT/Bedrock/SAP | ⚠️ Generic API support; testers wanted | Canonical API record, sometimes with required metadata |
| GitLab OAuth | ❌ Unsupported | Refresh rotates; stored record has no provably stable user identity |
| Snowflake OAuth | ❌ Unsupported | `accountId` identifies account/tenant and may not identify user |
| Well-known URL auth | ❌ Unsupported | Remote command/environment-token mechanism, not normal provider accounts |
| Custom/unknown OAuth | ❌ Unsupported | Shape alone cannot prove safe identity and refresh semantics |

Failing closed matters: assigning a rotated but unidentifiable live credential
to wrong saved account could destroy that saved account's previous credential.
Unsupported OAuth records raise `SchemaError` before live state is overwritten.

## Source evidence

- Common schemas and whole-file writes: `packages/opencode/src/auth/index.ts:14-36,58-89`
- Generic API-key loading: `packages/opencode/src/provider/provider.ts:1530-1541,1668-1715`
- OpenAI refresh/write-back: `packages/opencode/src/plugin/openai/codex.ts:355-380`
- xAI refresh/write-back: `packages/opencode/src/plugin/xai.ts:485-517`
- Snowflake refresh/write-back: `packages/opencode/src/plugin/snowflake-cortex.ts:298-367`
- Copilot durable token and zero expiry: `packages/opencode/src/plugin/github-copilot/copilot.ts:280-305`
- Built-in registration: `packages/opencode/src/plugin/index.ts:12-22,64-81`
