---
name: Bug report
about: Report a bug or unexpected behavior
title: "[Bug]: "
labels: ["bug"]
assignees: []
---
## Step 1: Are you in the right place?

* [ ] I have checked that there are no duplicate active or recent issues (bugs, questions, or feature requests) describing this problem.
* [ ] I am using the latest released version of `opencode-swap` (or have tested on the `main` branch).

> ⚠️ **Never paste credentials.** Do not include access tokens, refresh
> tokens, API keys, or the contents of `auth.json` in this issue. Account ids
> should only ever appear truncated to their last four characters, the same
> way `opencode-swap` itself prints them.

## Step 2: Describe your environment

* `opencode-swap --version`: `?`
* OpenCode version: `?`
* Install method (`uv tool install`, `pipx`, `uvx`, checkout): `?`
* Operating system and version: `?`
* Provider and auth type (e.g. `openai` ChatGPT OAuth, `zai-coding-plan` API key): `?`
* `opencode-swap doctor` output (sanitized, no paths containing your username if you'd rather redact them):

```text
<paste output here>
```

## Step 3: Describe the problem

### Steps to reproduce

1. ---
2. ---
3. ---

### Observed results

<!-- What happened? This could be a description, error message, or log output. -->

*

### Expected results

<!-- What did you expect to happen? -->

*

### Relevant code, configuration, or data

<!-- Paste the smallest possible code or config snippet that reproduces the issue. -->

```text
// Example snippet (replace with your own)
```

### Error / log output

<!-- If you are getting an error, paste the output here. Double-check it for anything credential-shaped before posting. -->

```text
<paste logs or error messages here>
```

<!-- Adding screenshots, screen recordings, or additional context is always helpful. -->
