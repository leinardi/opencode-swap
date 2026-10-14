# Provider support

The plugin lists and switches every credential OpenCode 2 stores, whatever the provider: it
reads OpenCode's credential list and calls `credential.activate`, both provider-independent.
What differs per provider is what it can show about an account, and whether it can look usage
up.

## What each credential shows

OpenCode 2 stores three credential types (`packages/schema/src/credential.ts:32-70` in OpenCode
2.0.29): `oauth {methodID, refresh, access, expires, metadata?}`, `key {key, metadata?,
configuration?}` and `external {methodID, metadata?}`.

| Integration and type | Picker description | Usage | Notes |
| --- | --- | --- | --- |
| `openai`, `oauth` (any method) | the email in the access token's claims | ChatGPT `wham/usage` | see [the open item](#open-items) on `chatgpt-token-sharing` |
| `zai-coding-plan`, `key` | the key's last 4 characters | z.ai GLM Coding Plan quota | the plain `zai` pay-as-you-go integration has no quota endpoint |
| any other integration, `key` | the key's last 4 characters | none |  |
| any other integration, `oauth` or `external` | none | none |  |
| a value the plugin does not recognize | none | none | still listed and switchable by label |

- **Key hints** appear only for keys of 20 or more characters, so at least 16 characters of a
  short, possibly hand-picked key always stay hidden.
- **Emails** come only from OpenAI access tokens: the `email` claim, or the one under
  `https://api.openai.com/auth`. Some real tokens carry neither, and then the row has no
  description. Other providers' tokens are not decoded.
- **Unrecognized values** (a credential type newer than the plugin, or a known type missing a
  field OpenCode's schema requires) are never read field by field: the account keeps its label
  and stays switchable, with no description and no usage.

## The footer's provider

The footer and `/swap-next` follow the current session's provider and map it to its
integration through OpenCode's provider list: `integrationID ?? id`
(`packages/core/src/model-resolver.ts:376-378`). In a sandbox, `openai`, `zai-coding-plan`,
`opencode`, `deepseek` and `github-copilot` all mapped to themselves; the OpenCode Console's
providers map to `opencode`. An integration whose active connection is an environment variable
(such as `OPENAI_API_KEY` or `ZHIPU_API_KEY`) shows no footer, since the stored label would name
the wrong credential. Details: [`opencode-v2-integration.md`](opencode-v2-integration.md), `V8`
and `V9`.

## Usage lookups

| Integration | Endpoint | Sent | Windows |
| --- | --- | --- | --- |
| `openai` | `GET https://chatgpt.com/backend-api/wham/usage` | access token as `Bearer`, `ChatGPT-Account-Id` when the token carries one | read from `limit_window_seconds` (today 5 hours and 7 days) |
| `zai-coding-plan` | `GET https://api.z.ai/api/monitor/usage/quota/limit` | API key as `Bearer` | each `CREDIT_LIMIT` (older responses: `TOKENS_LIMIT`) entry's `unit`/`number` (`unit` 3 = hour, 4 = day, 5 = 30 days, 6 = week) |

Window lengths are read from each response, never assumed, and label the footer's entries
(`5h`, `7d`). A response of any other shape is "unexpected response shape" and shows `n/a`.
These parsers are ports of the 0.4.x CLI's, with the differences recorded under "Deviations
and open items" in [`opencode-v2-integration.md`](opencode-v2-integration.md).

Adding a provider's usage needs its endpoint, its authentication and a documented or observed
response shape, plus fixed-text failures and leak tests like the existing two
(`test/usage-openai.test.ts`, `test/usage-zai.test.ts`). The plugin never refreshes a token to
look usage up: an expired access token shows `n/a` until OpenCode refreshes it.

## Open items

- **`chatgpt-token-sharing`.** OpenCode 2's default ChatGPT login stores this method; older
  logins, and those imported from OpenCode 1, use `chatgpt-browser`. Whether `wham/usage`
  accepts token-sharing tokens needs a real login to check. Until then every OpenAI OAuth
  method is eligible, and a refusal shows `n/a` and backs off.
- **Real usage numbers** from ChatGPT and z.ai accounts have not been seen with this plugin
  yet: the sandbox only ever had fake tokens, which both endpoints refuse.
