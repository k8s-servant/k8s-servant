<!--
PR title must follow Conventional Commits, e.g. `feat: add node pressure check` or
`fix(chart): drop nodes/proxy from ClusterRole`. It becomes the squash commit and the
changelog entry.
-->

## Summary

<!-- What does this change and why? -->

Closes #

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change (`!` in the title or a `BREAKING CHANGE:` footer)
- [ ] Docs / chore / CI

## How was it tested?

<!-- Commands you ran, new tests, cluster you tried it on (redacted). -->

## Checklist

- [ ] Tests added or updated, and `pytest` passes locally
- [ ] `ruff check` and `ruff format --check` pass
- [ ] Docs, chart values and examples updated where behavior changed
- [ ] The agent stays read-only: no write verbs, no Secret access, RBAC stays get/list/watch
- [ ] No secrets, kubeconfigs, internal hostnames or real cluster data in code, fixtures or screenshots
