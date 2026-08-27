# Security Policy

`opencode-swap` handles OpenCode provider credentials (access tokens, refresh
tokens, API keys). Report vulnerabilities responsibly — see below.

## Supported versions

Only the latest released version on PyPI is supported. Older releases do not
receive backported fixes; upgrade before reporting.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Use [GitHub's private vulnerability reporting](https://github.com/leinardi/opencode-swap/security/advisories/new)
for this repository (Security tab → "Report a vulnerability"). This opens a
private draft advisory visible only to the maintainer until a fix is ready.

If that is unavailable to you, email `roberto@leinardi.com` instead.

**Never include credentials, tokens, or `auth.json` contents in a report.** If
your report needs to show account state, sanitize it the same way
`opencode-swap` itself does: account ids truncated to their last four
characters, no raw token/key values, no `auth.json` attached. This mirrors the
handling contributors are asked to follow in
[`docs/provider-research-prompt.md`](docs/provider-research-prompt.md).

Please include:

- `opencode-swap --version` and OpenCode version.
- Operating system.
- Steps to reproduce, and the security impact you believe it has.

You'll get an acknowledgment as soon as practical. There is no bug bounty;
this is a personal open-source project.

## Threat model and scope

`opencode-swap` is designed to match OpenCode's own filesystem trust boundary,
not exceed it — it explicitly does not defend against a same-UID attacker, a
compromised kernel, or memory scraping. See
[`docs/security.md`](docs/security.md) for the full threat model, what's in
scope, and the deliberate design trade-offs (no custom cryptography, Linux
uses `chmod 0600` files rather than Secret Service, and the one known residual
race — switching accounts while OpenCode is mid-token-refresh — described in
the [README](README.md#commands)).

Findings that fall inside the "explicitly out of scope" section of
`docs/security.md` are still welcome as issues (not advisories) if you think
the boundary itself should move, but they are treated as design discussions,
not vulnerabilities.
