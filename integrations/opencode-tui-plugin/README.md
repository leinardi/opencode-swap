# opencode-swap OpenCode TUI plugin

Optional OpenCode terminal UI integration. `opencode-swap` remains sole owner
of credentials and account-switch transactions.

## Install from this checkout

Add this path to global `~/.config/opencode/tui.json`:

```json
{
  "$schema": "https://opencode.ai/tui.json",
  "plugin": ["/absolute/path/to/opencode-swap/integrations/opencode-tui-plugin/src/tui.tsx"]
}
```

OpenCode installs `@opencode-ai/plugin` for config-scoped local plugins when
needed. Restart the TUI after editing `tui.json`.

## Typecheck

Requires Bun 1.3.14. Root `make verify` installs locked dependencies and runs
the typecheck. To run only integration verification, use
`make tui-plugin-typecheck`.

Set a non-default CLI path through a plugin tuple:

```json
{
  "plugin": [["/absolute/path/to/opencode-swap/integrations/opencode-tui-plugin/src/tui.tsx", { "command": "/absolute/path/to/opencode-swap" }]]
}
```

## Behavior

- Shows `<account> · <usage>%` at right side of session prompt metadata.
- Shows nothing until session has sent a request using provider managed by
  `opencode-swap`.
- Uses latest sent user message's `model.providerID`, not internal OpenCode
  model-selection state. A model change appears after its next request.
- Refreshes every 60 seconds and after relevant session events. Usage is only
  requested while latest request used a managed provider.
- `/swap` and palette command **Switch OpenCode account** show account picker
  and confirmation dialog. `/swap-next` runs provider-local round-robin for
  latest request's managed provider. `/swap-refresh` refreshes visible status.
- Refuses a switch while current TUI session has non-idle status. It cannot
  observe other OpenCode sessions or processes; confirmation explains residual
  auth-file refresh race.

Plugin invokes `opencode-swap` through argument-array `Bun.spawn`, parses only
`status --json`, and never reads `auth.json`, registry, or secret storage.
