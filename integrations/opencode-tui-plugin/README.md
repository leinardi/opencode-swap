# opencode-swap OpenCode TUI plugin

Optional OpenCode terminal UI integration. `opencode-swap` remains sole owner
of credentials and account-switch transactions.

## Install from npm

Install the Python CLI first, then install TUI package globally:

```bash
uv tool install git+https://github.com/leinardi/opencode-swap.git
opencode plugin @leinardi/opencode-swap --global
```

Restart OpenCode. The plugin invokes `opencode-swap` from `PATH`.

## Install from this checkout

Add this path to global `~/.config/opencode/tui.json`:

```json
{
  "$schema": "https://opencode.ai/tui.json",
  "plugin": [
    "/absolute/path/to/opencode-swap/integrations/opencode-tui-plugin/src/tui.tsx"
  ]
}
```

OpenCode installs `@opencode-ai/plugin` for config-scoped local plugins when
needed. Restart the TUI after editing `tui.json`.

## Typecheck

Requires Bun 1.3.14. Root `make verify` installs locked dependencies and runs
typecheck and npm payload check. To run only integration verification, use
`make tui-plugin-typecheck` or `make tui-plugin-package-check`.

Set a non-default CLI path, or disable usage lookups (see "Network access"
below), through a plugin tuple. `command` must point directly to the
`opencode-swap` executable, not the project directory. For a checkout managed
by `uv`, the executable is normally `.venv/bin/opencode-swap`:

```json
{
  "plugin": [
    [
      "/absolute/path/to/opencode-swap/integrations/opencode-tui-plugin/src/tui.tsx",
      {
        "command": "/absolute/path/to/opencode-swap/.venv/bin/opencode-swap",
        "usage": false
      }
    ]
  ]
}
```

## Network access

By default, every 60-second refresh runs `opencode-swap status <provider>
--json --usage` for the session's active provider when it's a managed
account. That command sends **that account's live OAuth access token** as a
`Bearer` header to `https://chatgpt.com/backend-api/wham/usage` (see
`usage.py`), to fetch the usage percentage and reset time shown next to the
account name.

Set `{ "usage": false }` in the plugin options (see above) to disable this:
the widget then shows only the account name, and the plugin makes no network
calls at all — every other refresh continues to use plain `status --json`,
which is fully local/offline.

## Behavior

- Shows `<account> · <usage>% @<reset>` at right side of session prompt
  metadata. When OpenAI supplies its window duration and reset time, usage color
  compares spend against linear progress through that exact window: green below
  85% of projection, orange below 105%, and red at or above 105%. First 5% of
  the window stays green. Without complete window data, absolute usage is green
  below 50%, yellow from 50%, orange from 70%, and red from 90%.
- Shows nothing until session has sent a request using provider managed by
  `opencode-swap`.
- Uses latest sent user message's `model.providerID`, not internal OpenCode
  model-selection state. A model change appears after its next request.
- Refreshes every 60 seconds, whenever the active provider changes, and on
  `/swap-refresh`. Usage is only requested while latest request used a
  managed provider.
- `/swap` and palette command **Switch OpenCode account** show account picker
  and confirmation dialog. `/swap-next` runs provider-local round-robin for
  latest request's managed provider. `/swap-refresh` refreshes visible status.
- Refuses a switch while current TUI session has non-idle status. It cannot
  observe other OpenCode sessions or processes; confirmation explains residual
  auth-file refresh race.

Plugin invokes `opencode-swap` through argument-array `Bun.spawn`, parses only
`status --json` and, unless disabled, `status --json --usage` (see "Network
access"), and never reads `auth.json`, registry, or secret storage.
