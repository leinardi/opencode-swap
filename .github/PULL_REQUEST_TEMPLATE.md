<!-- markdownlint-disable MD041 -->
<!--
Any HTML comment will be stripped when the markdown is rendered, so you don't need to delete them.
-->

## Summary

<!-- Short description of what this PR does. -->

---

## Pull request checklist

- [ ] I am targeting the `main` branch
- [ ] I have **rebased** this branch on top of the destination branch
- [ ] I have executed `make check` locally *before creating the commit* and it has run successfully
- [ ] I have run `make verify` locally and it has passed
- [ ] Every commit message follows
  [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) with a scope
  (`type(scope): subject`)
- [ ] Each commit type, and this pull request's title, matches whether the change ships: `feat` or
  `fix` for what npm users should receive; never `perf` or `refactor` for a change that
  ships nothing, since commitizen bumps a patch for both
  ([versioning](https://github.com/leinardi/opencode-swap/blob/main/AGENTS.md#commit-messages))
- [ ] I have performed a self-review of my own code
- [ ] There are no `WIP` commits in this PR

---

## Type of changes

<!-- Tick all that apply: -->

- [ ] 🐛 Bug fix
- [ ] ✨ New feature
- [ ] 🔧 Refactoring
- [ ] 📜 Docs
- [ ] 🧰 CI / tooling / infra
- [ ] Other (describe in Summary)
