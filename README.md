# opencode-swap

**Discontinued.** opencode-swap switched [OpenCode](https://github.com/anomalyco/opencode)
between saved provider accounts. OpenCode 2 now stores several accounts per provider and switches
between them itself, so this project has ended. The repository is archived.

## If you use OpenCode 2

Remove both entries from `"plugins"` in `~/.config/opencode/cli.json`:

```text
"@leinardi/opencode-swap"
"opencode-swap"
```

1.x of `@leinardi/opencode-swap` is only a notice that asks you to do this. Remove both entries,
not just the first. OpenCode 2 copies OpenCode 1's `tui.json` plugins into `cli.json` on its first
start, turning the old `"plugin_enabled": {"opencode-swap": true}` into a bare `"opencode-swap"`
entry. While this package loads, that entry only enables it. Without it, OpenCode 2 installs
`opencode-swap` from npm, **an unrelated package**, and runs it.

What replaces each part of opencode-swap:

| opencode-swap | OpenCode 2 |
| --- | --- |
| saving, switching, renaming and removing accounts | `/connect`, `opencode auth login`, `opencode auth switch`, `opencode auth logout` |
| listing accounts | `opencode auth list` |
| moving accounts to another computer | `opencode auth export`, `opencode auth import` |
| usage and quota in the TUI | [`@slkiser/opencode-quota`](https://github.com/slkiser/opencode-quota) |

## Moving your saved accounts to OpenCode 2

The last CLI release, 0.5.0, moves the accounts saved with 0.4.x into OpenCode 2. Quit OpenCode 1
and remove the plugin from `tui.json` first (see above), then:

```bash
opencode-swap migrate-to-opencode2 | opencode auth import
```

Details and options are in the
[0.5.0 README](https://github.com/leinardi/opencode-swap/blob/v0.5.0/README.md#moving-to-opencode-2).
Once everything works in OpenCode 2, remove the saved accounts with
`opencode-swap remove <provider> <name>`, then uninstall the CLI (`uv tool uninstall opencode-swap`
or `pipx uninstall opencode-swap`).

## If you still use OpenCode 1

0.5.0 still works with OpenCode 1.x and gets no further changes. Keep the TUI plugin pinned in
`tui.json`, since a bare package name now resolves to the 1.x notice, which OpenCode 1 refuses to
load:

```json
{
  "plugin": ["@leinardi/opencode-swap@0.5"]
}
```

## The code

- The CLI and the OpenCode 1 TUI plugin: tag
  [`v0.5.0`](https://github.com/leinardi/opencode-swap/tree/v0.5.0).
- A finished OpenCode 2 plugin (a footer with the active account and pace-coloured usage, and a
  cross-provider account picker) that was never released: tag
  [`opencode2-plugin-final`](https://github.com/leinardi/opencode-swap/tree/opencode2-plugin-final).
  Its findings about OpenCode 2's plugin API, credentials and config migration are in
  [`docs/opencode-v2-integration.md`](https://github.com/leinardi/opencode-swap/blob/main/docs/opencode-v2-integration.md).

## License

MIT
