# OpenCode authentication storage (reverse-engineered ground truth)

Common storage behavior and OpenAI details here were verified by reading OpenCode's own source
(`packages/opencode/src/auth/index.ts`,
`packages/opencode/src/plugin/openai/codex.ts`,
`packages/core/src/global.ts`, `packages/core/src/fs-util.ts` — as of the
version checked out during this project's feasibility study) and
cross-checked against `opencode-balancer`'s independent reimplementation of
the same logic. It is **not** documented by OpenCode itself; the internals
below can change across OpenCode versions without notice, which is why
`opencode-swap` validates every record it reads (see
`docs/architecture.md#compatibility-posture`) instead of trusting this
document blindly at runtime.

Claims are marked:

- **(source)** — read directly from OpenCode's code, cited with file:line.
- **(empirical)** — observed against a real, live OpenCode installation
  during development (see the M0 technical spike).
- **(inferred)** — a reasonable conclusion from the above, not directly
  verified.

## Where credentials live

Single file, all providers together:

```
$XDG_DATA_HOME/opencode/auth.json
```

**(source)** `path.join(Global.Path.data, "auth.json")`
(`auth/index.ts:10`), where `Global.Path.data = path.join(xdgData, "opencode")`
(`global.ts:3,11-12`). Default resolves to
`~/.local/share/opencode/auth.json` on both Linux and macOS — **(source)**
there is no macOS-specific path and no OS keychain involved on OpenCode's
side; it's a plain file on every platform OpenCode runs on.

Env var behavior **(source)**:

- `XDG_DATA_HOME` moves the whole data dir (and therefore `auth.json`).
- `OPENCODE_CONFIG_DIR` overrides OpenCode's *config* dir only — it does
  **not** move `auth.json` (`global.ts:64`).
- `OPENCODE_TEST_HOME` overrides OpenCode's notion of home
  (`global.ts:19`) — `opencode-swap`'s `paths.py` honors this too, so tests
  and manual verification can point both tools at the same fake home.
- `OPENCODE_AUTH_CONTENT` — if set in the *OpenCode process's* environment,
  `Auth.all()` parses this JSON and **ignores the file entirely**
  (`auth/index.ts:59-63`). If this is set, `opencode-swap`'s file-based
  swap has no effect on that OpenCode process. `opencode-swap` can only
  detect whether *its own* process has this set (`doctor` warns if so) —
  it cannot know whether some other OpenCode process has it set.

## Common record shapes

`auth.json` is a flat JSON object keyed by provider id. The OpenAI entry,
when logged in via ChatGPT OAuth, has this shape **(source,
`auth/index.ts:14-21`)**:

```json
{
  "openai": {
    "type": "oauth",
    "refresh": "<refresh token>",
    "access": "<JWT access token>",
    "expires": 1730000000000,
    "accountId": "<chatgpt_account_id, optional>"
  }
}
```

Two other shapes exist for provider keys **(source)**:
`{"type": "api", "key": "...", "metadata"?: {...}}` for a manually-entered
API key, and `{"type": "wellknown", "key": "...", "token": "..."}`. All
other provider keys (e.g. `"anthropic"`) coexist in the same flat object and
are untouched by operations targeting another provider. Shared shape does not
imply shared OAuth behavior; see `docs/provider-support.md`.

`accountId`, when present, comes from the JWT's `chatgpt_account_id` claim,
or `["https://api.openai.com/auth"].chatgpt_account_id`, or
`organizations[0].id`, tried in that order **(source, `codex.ts:57-76`)**.

An `email` claim is referenced in OpenCode's `IdTokenClaims` type
(`codex.ts:41`), but **(empirical)** a real access token captured during
development did not carry one — `opencode-swap`'s email extraction
therefore always treats it as optional and expect it to often be absent in
practice; don't rely on it being populated.

## Loading and refresh

- `Auth.all()` re-reads the file from disk **every call** — no in-memory
  cache **(source, `auth/index.ts:58-67`)**.
- The OpenAI request path calls `getAuth()` fresh on every outbound request
  **(source, `codex.ts:355`)**. This is why a file swap takes effect on the
  next request without restarting OpenCode, as long as OpenCode is idle
  when the swap happens — **(empirical)** confirmed directly: swapping the
  live file and immediately issuing an `opencode run` picked up the new
  account on that very invocation.
- If the access token is missing or `expires < now`, OpenCode refreshes it
  itself: `POST {issuer}/oauth/token`, `grant_type=refresh_token`,
  `client_id=app_EMoamEEZ73f0CkXaXp7hrann`, against
  `https://auth.openai.com` **(source, `codex.ts:125-139`)**. This is a
  **public PKCE OAuth client with no client secret**, which is what makes a
  standalone refresh from `opencode-swap` itself safe to add: `oauth_refresh.py`
  makes this exact request for accounts OpenCode doesn't currently have live
  (see `docs/roadmap.md`'s "Known gaps" for where it is and isn't triggered).
  For whichever account *is* currently live in OpenCode, `opencode-swap`
  still never refreshes it standalone — OpenCode already refreshes on its
  own next request, and its stored refresh token has likely already been
  consumed by that very rotation.
- On a successful refresh, OpenCode **writes the rotated tokens back** —
  both `access` and `refresh` (OpenAI issues a new refresh token on every
  use) — through the same write path as any other update
  **(source, `codex.ts:366-375`, `auth/index.ts:73-81`)**.

This refresh-and-rewrite behavior is the entire reason `opencode-swap`'s
`use_account` performs sync-back before every switch: whichever account was
live may have had its tokens silently rotated since it was last saved.

## Writes: no lock, not atomic

`Auth.set` does a **read-merge-write with no file lock**: read the whole
file, merge in the one key being updated, write the whole file back
**(source, `auth/index.ts:73-81`)**. The write itself is
**(source, `fs-util.ts:110-114`)**:

```
writeFileString(path, json)   // truncate + write, not atomic
if mode: chmod(path, mode)     // a SEPARATE syscall, after the write
```

Two consequences `opencode-swap` designs around:

1. **No cooperative lock exists to join.** If `opencode-swap` swaps
   `auth.json` at the exact moment OpenCode is mid-refresh-write, there's a
   real (if narrow) race. `opencode-swap` mitigates this by making its own
   write atomic and by warning/prompting when it detects a running
   `opencode` process (`process_detection.py`) — but this is advisory, not
   a real mutex, because OpenCode gives external tools nothing to
   synchronize against.
2. **A crash mid-write on OpenCode's side could leave a truncated file** —
   this is OpenCode's own behavior, not something `opencode-swap` can fix.
   `opencode-swap`'s `doctor`/`restore` commands exist partly as a recovery
   path for exactly this kind of corruption, regardless of which side
   caused it.

There is no schema version field, and entries that fail schema decode are
silently dropped by `Auth.all()`'s `filterMap` **(source, `auth/index.ts:66`)**
— i.e. OpenCode itself tolerates an unrecognized/malformed entry by quietly
ignoring it. `opencode-swap` deliberately does **not** copy this leniency
(see `docs/architecture.md#compatibility-posture`): since it's about to
*overwrite* state, silently ignoring a malformed entry could mean silently
discarding a real account.

## Login flow (context, not load-bearing for the swap itself)

OpenCode supports three OpenAI auth methods **(source, `codex.ts:429-547`)**:
ChatGPT OAuth via a local loopback server on port 1455, ChatGPT OAuth via a
device-code flow (headless), and a manually-entered API key.
`opencode-swap` never drives this flow itself — it only ever reads whatever
OpenCode already wrote after a normal `opencode auth login`. This keeps
`opencode-swap` decoupled from OpenCode's login UI/UX entirely; the only
coupling is to the `auth.json` file format described above.
