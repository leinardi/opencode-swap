# Security Policy

`@leinardi/opencode-swap` runs inside OpenCode's TUI with access to every credential OpenCode
stores (access tokens, refresh tokens, API keys). Report vulnerabilities responsibly, as below.

## Supported versions

| Version | Supported |
| --- | --- |
| latest 1.x on npm (OpenCode 2) | yes |
| 0.4.x TUI plugin on npm (OpenCode 1, `maint/0.4`) | security fixes only |
| `opencode-swap` CLI on PyPI (0.4.7 and older) | no: retired |

Upgrade to the latest release of your line before reporting.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Use [GitHub's private vulnerability reporting](https://github.com/leinardi/opencode-swap/security/advisories/new)
for this repository (Security tab → "Report a vulnerability"). This opens a private draft
advisory visible only to the maintainer until a fix is ready.

If that is unavailable to you, email `roberto@leinardi.com` instead.

**Never include credentials in a report**: no tokens, no API keys, no `opencode auth export`
output, no `opencode.db`. If the report needs to show account state, sanitize it the way the
plugin itself does: a key as its last four characters at most, no raw token or key values.

Please include:

- the `@leinardi/opencode-swap` version and the OpenCode version (`opencode --version`);
- the operating system;
- steps to reproduce, and the security impact you believe it has.

You'll get an acknowledgment as soon as practical. There is no bug bounty; this is a personal
open-source project.

## Threat model and scope

The plugin matches OpenCode's own trust boundary rather than exceeding it: it does not defend
against a same-user attacker, root, memory scraping, OpenCode itself or another plugin, all of
which already have the credentials. What it does guarantee, and what is out of scope, is in
[`docs/security.md`](docs/security.md).

Findings in that document's "Out of scope" section are still welcome as issues (not advisories)
if you think the boundary itself should move, but they are treated as design discussions, not
vulnerabilities.
