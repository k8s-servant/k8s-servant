# CI and Releases

How the automation in `.github/workflows/` fits together: what each workflow runs, why
it is there, whether it blocks a merge, and how to run the same check locally before
you push.

## Contents

- [The big picture](#the-big-picture)
- [CLI, Action or GitHub service](#cli-action-or-github-service)
- [Required checks and CI result](#required-checks-and-ci-result)
- [ci.yml: does the code work](#ciyml-does-the-code-work)
- [security.yml: is anything dangerous coming in](#securityyml-is-anything-dangerous-coming-in)
- [codeql.yml: deeper static analysis](#codeqlyml-deeper-static-analysis)
- [pr-title.yml: Conventional Commit titles](#pr-titleyml-conventional-commit-titles)
- [Claude workflows](#claude-workflows)
- [release-please.yml: versions and published artifacts](#release-pleaseyml-versions-and-published-artifacts)
- [Housekeeping: Scorecard, labels, Dependabot](#housekeeping-scorecard-labels-dependabot)
- [Conventions every workflow follows](#conventions-every-workflow-follows)
- [Cheat sheet](#cheat-sheet)

## The big picture

```
PR opened or updated ──► ci · security · codeql · pr-title · claude-review
                           required to merge: CI result, gitleaks, zizmor, pr-title
merge (squash; PR title = commit message) ──► main
                           └─► release-please opens or updates the release PR
merge the release PR ──► tag vX.Y.Z ──► signed image + signed Helm chart on GHCR
weekly ──► security scans · CodeQL · Scorecard · Dependabot PRs
```

| When | What runs | Publishes |
|---|---|---|
| PR opened or updated | lint, tests (3 Python versions), helm, docker build + Trivy, gitleaks, zizmor, dependency-review, CodeQL, pr-title, Claude review | nothing |
| Merge to `main` | the same CI and security checks, CodeQL, Scorecard, release-please | nothing (the release PR is updated) |
| Release PR merged | release-please, then our publish jobs | tag, GitHub Release, image, chart |
| Weekly | security scans, CodeQL, Scorecard, Dependabot | Dependabot PRs |

## CLI, Action or GitHub service

Most of the tools are ordinary command-line programs. A workflow is a recipe that
installs a tool on a throwaway Linux VM, runs it against the code, and turns its exit
code into a green or red check. Three kinds appear here:

| Kind | Meaning | Examples |
|---|---|---|
| **Standalone CLI** | A program you can install and run locally; CI runs the same command | ruff, gitleaks, zizmor, trivy, helm, pytest, docker, cosign |
| **Action wrapping a CLI** | A packaged GitHub Action that installs and runs a CLI, adding annotations or uploads | trivy-action, zizmor-action, codeql-action, release-please-action, claude-code-action |
| **GitHub service** | Logic inside GitHub itself; nothing to run locally, the Action only calls GitHub's API | dependency-review, Dependabot, secret scanning, code scanning |

## Required checks and CI result

The `main` ruleset lists the check names that must be green before a PR can merge:
`CI result`, `gitleaks`, `zizmor` and `pr-title`. All review conversations must also be
resolved.

`ci.yml` produces six checks of its own: `lint`, `test (py3.12)`, `test (py3.13)`,
`test (py3.14)`, `helm` and `docker`. Requiring each of them would cause two problems:

- **Renames break merging.** Adding Python 3.15 creates a check nobody requires;
  dropping 3.12 leaves the ruleset waiting forever for `test (py3.12)`, and every PR is
  stuck.
- **Skipped counts as passed.** GitHub treats a skipped required job as success, so a
  job skipped after an earlier failure could make a PR look mergeable.

So `ci.yml` ends with a small job, `CI result`, that waits for the others and checks how
each one ended. The ruleset requires only that name:

```yaml
result:
  name: CI result
  if: always()                         # run even when a dependency failed
  needs: [lint, test, helm, docker]
  steps:
    - run: |
        for r in ${RESULTS}; do           # e.g. "success success failure success"
          [ "$r" = success ] || exit 1    # failure, cancelled or skipped -> red
        done
```

CI jobs can be added, renamed or split freely; only `needs:` changes, never the ruleset.

## ci.yml: does the code work

Runs on every PR and every push to `main`.

### lint: ruff

[ruff](https://docs.astral.sh/ruff/) is a single fast CLI that is both a **linter**
(likely bugs and bad patterns) and a **formatter** (code layout). It replaces flake8,
isort, black, pyupgrade and part of bandit. The rule set is in `pyproject.toml`
(`[tool.ruff]`).

```bash
ruff check .            # pycodestyle, pyflakes, isort, bugbear, pyupgrade, bandit, simplify, ruff
ruff format --check .   # fails if any file is not formatted
```

Locally: `ruff check --fix . && ruff format .`, or `pre-commit install` to run it on
every commit.

### test: pytest

The full test suite on Python 3.12, 3.13 and 3.14 in parallel (a matrix), with coverage.
Helm is installed first so the chart tests render the chart instead of being skipped.

### helm

`helm lint --strict` and `helm template` with the files in `examples/`, so a broken
template or value fails here instead of on a user's cluster.

### docker: buildx and Trivy

- Builds the image (amd64) and runs `k8s-servant --help` inside it. The image is thrown
  away; nothing is pushed.
- [Trivy](https://trivy.dev/) scans the image's Alpine packages, Python packages and Go
  binaries (kubectl) for known vulnerabilities.
- Fixable **CRITICAL** findings fail the check. **HIGH** findings are reported only,
  because upstream kubectl builds often lag behind Go security patches for weeks.

Locally:

```bash
docker build -t k8s-servant:dev .
trivy image --severity HIGH,CRITICAL --ignore-unfixed k8s-servant:dev
```

## security.yml: is anything dangerous coming in

Runs on every PR, every push to `main`, and weekly.

### gitleaks (required)

[gitleaks](https://github.com/gitleaks/gitleaks) is a CLI that finds secrets (cloud
keys, GitHub tokens, private keys, passwords in URLs) with regex rules and an entropy
check. CI scans **every commit in history**, because in a public repository a secret
removed in a later commit is still readable in the earlier one:

```bash
gitleaks git --redact --verbose --exit-code 1 .
```

`--redact` keeps the secret itself out of the CI log. The workflow downloads the release
binary and verifies its checksum instead of using `gitleaks-action`, which needs a paid
license for organization accounts.

gitleaks is one of three layers: GitHub **push protection** rejects a push that contains
a recognized token, the **pre-commit hook** catches secrets on your laptop, and gitleaks
in CI catches the rest.

Locally: `gitleaks git .` (history) or `gitleaks dir .` (files only).

### zizmor (required)

[zizmor](https://docs.zizmor.sh/) is a CLI that audits the workflow files themselves.
Workflows hold write tokens and secrets, so they get their own linter. It catches:

- **template injection**, e.g. `run: echo ${{ github.event.issue.title }}`, which lets
  anyone who opens an issue run shell commands in CI;
- **excessive permissions** on jobs;
- **unpinned actions** (`@v1` instead of a commit SHA);
- **leaked checkout credentials** (missing `persist-credentials: false`);
- **dangerous triggers** such as `pull_request_target` and `issue_comment`. The one use of
  `issue_comment`, in `claude.yml`, is gated to trusted authors and carries an inline
  ignore that explains why;
- **actions with known vulnerabilities** (online audit).

Locally: `uvx zizmor .`

### dependency-review (PRs only, reports)

A GitHub service, not a CLI:

1. GitHub's **dependency graph** parses the manifests: `pyproject.toml`, the `uses:` lines
   in workflows, and others.
2. On a PR, the Action asks GitHub's API for the **difference** between the graph of the
   base branch and the PR head: packages added, removed or changed.
3. Each added or changed package is checked against the **GitHub Advisory Database** and
   its declared license.
4. The check fails on a new dependency with a **HIGH or CRITICAL** advisory, or a license
   that cannot ship under Apache-2.0 (GPL-3.0, AGPL-3.0), and writes a summary on the PR.

Its limit: it sees only what the manifests declare. Without a lockfile it checks the
version ranges in `pyproject.toml`, not every transitive package; Trivy covers what
actually ends up in the image.

## codeql.yml: deeper static analysis

Runs on every PR, every push to `main`, and weekly, for **Python** (our code) and
**Actions** (our workflows), with the `security-and-quality` query suite. Results appear
in the Security tab and as PR annotations.

CodeQL and SonarQube are both static analysis, with a different focus:

| | CodeQL | SonarQube |
|---|---|---|
| Main focus | Security | Code quality |
| How | Builds a database of the code and runs queries over it | Rules plus dashboards |
| Strength | Data flow: does untrusted input reach a dangerous call? | Smells, duplication, complexity, coverage trends, quality gates |
| Running it | Built into GitHub, free for public repositories | A server or SonarCloud to host |

With ruff covering style and quick bugs, CodeQL covers the deeper flow analysis.

## pr-title.yml: Conventional Commit titles

`pr-title` is a validator that makes every PR follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) before it can land
on `main`.

### Why the title, not the commits

- PRs are **squash-merged only**: the commits on a branch ("wip", "fix typo") are
  squashed away, so contributors never have to rewrite history.
- The repository is set so the squash commit message is the **PR title**: only the title
  reaches `main`, as one commit.
- **release-please** reads the commits on `main` to choose the next version and write the
  changelog.

### Where the rules come from

| Layer | Where | What it decides |
|---|---|---|
| Format | The [Conventional Commits spec](https://www.conventionalcommits.org/en/v1.0.0/) | The grammar `type(scope)!: subject` |
| Parser | `amannn/action-semantic-pull-request` | Splits a title into its parts |
| Our settings | `.github/workflows/pr-title.yml` | Allowed types, whether a scope is required, the subject pattern |
| Release meaning | `release-please-config.json` | Which types bump the version and which changelog section they go in |

Our settings:

```yaml
types: |              # allowed "type" values
  feat
  fix
  docs
  refactor
  perf
  test
  build
  ci
  chore
  revert
requireScope: false              # "(scope)" is optional, and any scope is accepted
subjectPattern: ^(?![A-Z]).+$    # the subject starts with a lowercase letter
```

When you add a type to `pr-title.yml`, add it to `changelog-sections` in
`release-please-config.json` too, or its commits will not show in the changelog.

### How the check works

1. It runs when a PR is opened, gets new commits, or **its title is edited**. Fixing the
   title re-runs the check by itself.
2. It reads the title from the event payload and never checks out code, so it is safe on
   fork PRs.
3. It parses `type(scope)!: subject`: a type from the list, an optional scope, `!` for a
   breaking change, and a lowercase subject.
4. The result is the required `pr-title` check.

You can **open** a PR with any title, but it cannot be **merged** until the check passes.
Organization admins can bypass the ruleset; do not, because a commit with a bad title
does not show in the changelog or count toward the version.

| Title | Result |
|---|---|
| `feat: add Slack notifier` | valid: minor bump, under "Features" |
| `fix(chart): drop nodes/proxy from ClusterRole` | valid: patch bump, under "Bug fixes" |
| `feat!: rename FOUNDRY_* settings to LLM_*` | valid: breaking change |
| `docs: explain Workload Identity setup` | valid: changelog entry, no release on its own |
| `Add Slack notifier` | invalid: no type |
| `feature: add Slack` | invalid: `feature` is not an allowed type |
| `fix: Fix typo` | invalid: the subject starts with a capital |

## Claude workflows

### claude-review.yml: automatic review

On every non-draft PR from a branch in this repository, Claude reads the diff and
[CLAUDE.md](../CLAUDE.md) and reviews in priority order: the read-only guarantee, then
correctness, security, tests and docs. Findings arrive as inline comments plus one
summary. The review is not a required check, but its inline comments are conversations
that must be resolved before merging.

Fork PRs are skipped: they get no secrets, and running a model with write access on code
from strangers invites prompt injection. Dependabot PRs are skipped too.

### claude.yml: @claude on demand

Write `@claude` in an issue, a PR comment or a review, and Claude answers, explains, or
pushes a fix to a branch. Only the owner, organization members and collaborators can
trigger it, because the job has write permissions and the Claude credential (the
`CLAUDE_CODE_OAUTH_TOKEN` secret and the Claude GitHub App).

Before Claude starts, the job installs Python, helm and the dev dependencies, and
allows Claude to run exactly the checks from `CLAUDE.md`: `.venv/bin/pytest`,
`.venv/bin/ruff`, `helm lint` and `helm template`. So Claude tests its change before it
pushes, and CI then confirms. On a **fork PR** this is switched off: Claude can still
read and comment, but it runs nothing, because the fork's code would run next to the
Claude credential.

Typical flow on an issue: comment `@claude implement this`. Claude works on a branch
`claude/issue-N-…`, commits as `claude[bot]`, and replies with a **Create PR** link.
You open the PR (check that the title is a Conventional Commit), and the normal CI and
review run on it.

## release-please.yml: versions and published artifacts

Runs on every push to `main`. The work is split between Google's
[release-please](https://github.com/googleapis/release-please) bot and our own jobs:

1. **The bot** reads the commits since the last tag. After a normal merge it opens or
   updates one PR, `chore(main): release X.Y.Z`, which bumps the version in
   `pyproject.toml` and `charts/k8s-servant/Chart.yaml` and writes `CHANGELOG.md`.
2. **You** merge that release PR when you want to ship. The bot never merges anything.
3. On that merge the bot tags `vX.Y.Z`, creates the GitHub Release and sets
   `release_created=true`.
4. **Our jobs** run only when `release_created` is true:
   - `publish image` builds amd64 and arm64 images, pushes
     `ghcr.io/k8s-servant/k8s-servant:X.Y.Z`, `:X.Y` and `:latest` with an SBOM and a
     provenance attestation, and signs them with cosign (keyless, tied to this
     workflow's identity);
   - `publish chart` packages, pushes and signs the chart at
     `oci://ghcr.io/k8s-servant/charts/k8s-servant`.

The bot knows only about versions, changelogs, tags and releases. Anything else that
should happen on a release is another job with the same `if:` condition.

### Version rules

| Commit type | Before 1.0 | After 1.0 |
|---|---|---|
| `fix:` | 0.1.0 → 0.1.1 | 1.2.3 → 1.2.4 |
| `feat:` | 0.1.0 → 0.2.0 | 1.2.3 → 1.3.0 |
| `feat!:` or a `BREAKING CHANGE:` footer | 0.1.0 → 0.2.0 | 1.2.3 → 2.0.0 |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:` | no release on their own | same |

Before 1.0, breaking changes bump the minor version (`bump-minor-pre-major` in
`release-please-config.json`).

### Files

| File | Role |
|---|---|
| `release-please-config.json` | Release type, changelog sections, extra files to bump |
| `.release-please-manifest.json` | The current version |
| `# x-release-please-version` comments | Mark the version lines the bot rewrites |
| `CHANGELOG.md` | Generated by the bot; do not edit it by hand |

Regular PRs and merges to `main` build the image but never push it. Only a release
publishes.

## Housekeeping: Scorecard, labels, Dependabot

- **scorecard.yml**: [OpenSSF Scorecard](https://scorecard.dev/) grades the repository
  against supply-chain practices (branch protection, pinned actions, signed releases,
  security policy, code review) weekly and on push to `main`. It publishes the score
  shown in the README badge and puts findings in the Security tab.
- **labels.yml**: makes the repository's labels match `.github/labels.yml` whenever that
  file changes, so labels are reviewed in git instead of edited by hand.
- **Dependabot** (`.github/dependabot.yml`, a config file, not a workflow): weekly grouped
  PRs for Python packages, GitHub Actions SHAs and the Docker base image, with a 7-day
  cooldown so brand-new releases are not picked up at once. It does not update the
  kubectl version (a Dockerfile build argument).

## Conventions every workflow follows

| Rule | What it looks like | Why |
|---|---|---|
| Pin actions to a commit SHA | `uses: actions/checkout@3d3c42e… # v7.0.1` | A tag can be moved to malicious code; a SHA cannot. The organization enforces it, and Dependabot keeps SHAs current |
| No permissions by default | `permissions: {}` at the top, then per job | Each job gets only what it needs, usually `contents: read` |
| Do not leave the git token on disk | `persist-credentials: false` | Later steps cannot reuse the checkout token to push |
| Cancel stale runs | `concurrency: … cancel-in-progress` | A new push to a PR stops the previous run |
| Allow-listed actions only | Organization settings → Actions | Only GitHub, verified creators and a named list can run |
| Trusted triggers only | No `pull_request_target`; `@claude` gated by author | Code from forks never runs with secrets or write access |

## Cheat sheet

| Check | Kind | Blocks merge | Run locally |
|---|---|---|---|
| `CI result` (lint, test, helm, docker) | CLIs | yes | `ruff check . && pytest && helm lint charts/k8s-servant` |
| `gitleaks` | CLI | yes | `gitleaks git .` |
| `zizmor` | CLI | yes | `uvx zizmor .` |
| `pr-title` | Action | yes | check the title against `type: subject` |
| `dependency-review` | GitHub service | no (reports) | n/a |
| `CodeQL` | CLI + service | alerts show on the PR | possible with the CodeQL CLI; rarely needed |
| `review` (Claude) | Action | no, but its threads must be resolved | Claude Code in your terminal |
