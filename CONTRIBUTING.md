# Contributing to k8s-servant

Thanks for your interest! Bug reports, docs fixes, new skills and code are all welcome.

## Ground rules

- Be kind. This project follows the [Code of Conduct](https://github.com/k8s-servant/.github/blob/main/CODE_OF_CONDUCT.md).
- **Security issues go through [private vulnerability reporting](https://github.com/k8s-servant/k8s-servant/security/advisories/new)**, never public issues.
- **The agent is read-only.** Changes that let it modify cluster objects or read
  Secrets will not be accepted.
- Never paste real cluster data, kubeconfigs, tokens or internal hostnames into issues,
  PRs, test fixtures or examples.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,azure]'
.venv/bin/pre-commit install      # ruff + gitleaks on every commit
.venv/bin/pytest                  # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
helm lint charts/k8s-servant      # chart
docker build -t k8s-servant:dev . # image
```

## Workflow

1. For anything bigger than a small fix, open an issue first so we can agree on the approach.
2. Fork, create a branch, and write a failing test before the code (one test file per module).
3. Keep PRs focused. Update docs alongside behavior changes.
4. **PR titles follow [Conventional Commits](https://www.conventionalcommits.org/)**
   (`feat: ...`, `fix(chart): ...`, `docs: ...`). PRs are squash-merged, and the title
   becomes the commit message that drives the changelog and version bumps.
   A breaking change uses `feat!:` or a `BREAKING CHANGE:` footer.
5. CI must be green: lint, tests, Helm, Docker build, security scans. What each check
   does and how to run it locally: [docs/ci-and-releases.md](docs/ci-and-releases.md).

## AI-assisted contributions

Using Claude Code or another assistant is fine. [`CLAUDE.md`](CLAUDE.md) holds the
project conventions those tools should follow. You remain responsible for every line
you submit. Maintainers can also mention `@claude` on issues and PRs.

## Releases

Maintainers merge the release PR that release-please keeps open (details in
[docs/ci-and-releases.md](docs/ci-and-releases.md#release-pleaseyml-versions-and-published-artifacts)). That tags the release,
publishes the signed image to `ghcr.io/k8s-servant/k8s-servant`, and pushes the Helm
chart to `oci://ghcr.io/k8s-servant/charts`.
