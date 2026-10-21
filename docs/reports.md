# Health checks, the morning report and Teams cards

The agent produces two kinds of reports:

| | Health check | Morning report |
|---|---|---|
| Answers | "What is wrong right now, and what changed?" | "How did the night go, and what needs attention today?" |
| When | every `CHECK_INTERVAL_MINUTES` (default 60) in `serve`; on demand with `k8s-servant check` | weekdays at `MORNING_REPORT_TIME` (08:00 UTC by default) in `serve`; on demand with `k8s-servant report` |
| Teams | only when something is new or resolved (`TEAMS_POST_MODE=changes`) | always (unless `TEAMS_POST_MODE=off`) |
| Stored | run of kind `check` | run of kind `morning` |

Both are printed to stdout and stored in the run history (`k8s-servant history <id>`).

## The health check

### Flow

```
1. Sweep without the model: cluster_problems + alertmanager_alerts          (a few seconds)
   scheduled check, same result as the last full check, last full check < 6 h ago?
       → recorded as "unchanged", open findings stay open, nothing posted. Done.
2. The agent runs: "follow the cluster-health-check skill; these findings were open
   before: ...; end with a JSON block of findings"
3. The JSON is read. Missing or invalid → one follow-up asking only for the JSON.
   Still invalid → the run is recorded as failed and memory is NOT changed.
4. Memory compares with the open findings → new / still open / resolved.
5. Markdown report → stdout and history; Teams card per TEAMS_POST_MODE.
```

The model is told the previously open findings so it re-checks each one and reuses
its fingerprint (see below).

### Findings and fingerprints

Every problem the model reports is a **finding**:

```json
{
  "fingerprint": "pdb/shop/poddisruptionbudget/orders-api",
  "severity": "warning",
  "title": "PodDisruptionBudget orders-api matches no pods",
  "detail": "At 2026-10-12 11:50 UTC ... expectedPods=0 ... fix by deleting the stale PDB or restoring the workload."
}
```

- **Fingerprint** = `<category>/<namespace or _cluster>/<Kind>/<name>`, normalised to
    lower case. It identifies *what* is broken, not when or how it was seen, so the same
    problem keeps the same identity across checks and pod restarts. It names the owning
    workload, not the pod.
- **Categories:** `crashloop`, `pending`, `oom`, `image-pull`, `not-ready`, `rollout`,
    `job-failed`, `node-not-ready`, `node-pressure`, `capacity`, `pvc`, `flux-not-ready`,
    `alert-firing`, `quota`, `pdb`, `certificate`, `other`.
- **Severity:** `critical` (user impact likely: node NotReady, system component down,
    zero ready replicas, PVC lost), `warning` (degraded but working), `info`.

How memory turns fingerprints into new / still open / resolved:
[memory.md](memory.md#findings-new-still-open-resolved).

### Example report (test cluster, 2026-10-12)

```
# Health check — my-cluster — 2026-10-12 11:49 UTC

The cluster is broadly healthy. ... the only still-open problems are two
PodDisruptionBudgets that match no pods and are firing alerts.

**2 new · 2 open · 0 resolved**

## New

- **[WARNING] PodDisruptionBudget orders-api matches no pods** — `pdb/shop/poddisruptionbudget/orders-api`
  At 2026-10-12 11:50 UTC, cluster_problems reported ... status.expectedPods=0 ...
  Alertmanager also had KubePdbNotEnoughHealthyPods firing for namespace=shop
  since 2026-09-23. Likely the workload was removed or scaled to zero; fix by deleting
  the stale PDB or restoring the matching workload.
```

### When the model is skipped

A full health check on a large test cluster costs 150k–225k model tokens and about a minute; most
hours nothing changes. So **scheduled** checks first run the summary tools without the
model and compare a fingerprint of:

- the problem lines of `cluster_problems` (not its header, which has a timestamp and
    object counts);
- the firing alerts' name, severity and location (not their start time or text, which
    contain live values), without `ALERTS_IGNORE_NAMES` (`InfoInhibitor` re-fires every
    few minutes).

If it equals the fingerprint of the last **successful full** check, and that check is
less than `CHECK_FULL_INTERVAL_HOURS` (default 6) old, the run is recorded as:

```
Unchanged since the full check at 2026-10-12 12:27 UTC: the same problems and firing
alerts, so the model was not called.
```

The open findings stay open, nothing is posted, no tokens are used (about 6 s).

A full check still runs:

- when anything in the summary or the alerts changed;
- at least every `CHECK_FULL_INTERVAL_HOURS`, because the summary cannot see
    everything the model checks (Flux, node usage, events);
- after a failed check, or when the summary could not read everything;
- always for `k8s-servant check` (manual) and the morning report's own check.

Turn skipping off with `CHECK_SKIP_UNCHANGED=false`.

### When a check fails

| What failed | Recorded as | Memory | Teams |
|---|---|---|---|
| The model is unreachable (401, 403, 404, 429, network) | failed run with an explanation (`k8s-servant check` exits 1) | unchanged | not posted; the error is logged |
| No valid findings JSON after one retry | failed run with the model's answer | unchanged | posted only with `TEAMS_POST_MODE=always` |
| A tool fails | nothing: the model gets `ERROR: ...` and continues | — | — |

A failed check never changes memory: otherwise an empty answer would mark every open
problem as resolved and post a misleading "all resolved".

## The morning report

### What it covers

The time **since the previous morning report**: on Tuesday to Friday the night, on
Monday the weekend (reports are sent on weekdays only). At most 72 hours; 24 hours
when there was no earlier report.

### How it is built

1. **A re-scan of the cluster** (`SCAN_BEFORE_REPORT`, on by default): no model, no
      tokens. What changed since the last scan becomes the "Since the last scan"
      section ([below](#since-the-last-scan)).
2. A fresh full health check, so the report reflects the cluster right now. If it
      fails, the report still goes out and says so.
3. From memory, for the window:
      - **open findings** and how long each has been open;
      - **new overnight, still open**;
      - **resolved overnight** (open before the window);
      - **came and went overnight**: appeared and resolved within the window, with times.
          These are easy to miss and often worth a look (a rollout blocked by a quota for
          three minutes, a pod crash-looping at 03:00);
      - **health checks**: how many ran in full, were skipped as unchanged, failed.
4. **Alerts firing now** (Alertmanager), without `ALERTS_IGNORE_NAMES`.
5. **Alerts that fired overnight and stopped**: from Prometheus' `ALERTS` history for
      the window (`max_over_time(ALERTS{alertstate="firing"}[window])`). Only warning and
      critical; info alerts are muted by Alertmanager too, and a large cluster can fire dozens per
      night. Needs `PROMETHEUS_URL`.
6. **Notes the agent saved overnight**, so people can remove wrong ones.
7. **The overview:** the model (no tools) writes 3–6 sentences from these facts only:
      how the night went, what needs attention today (most important first), and what
      recovered by itself but is worth a look.

### Example (test cluster, 2026-10-12)

```
# Morning report — my-cluster — Mon 12 Oct 2026

_Covers the time since Sun 11 Oct 15:47 (24 h)._

The cluster was mostly quiet and the latest check is ok, but there are 4 open warnings
and 2 alerts firing now. The most urgent item today is the node
aks-nodepool1-12345678-vmss000003, which is still at 99% memory. Also check the azure-cns
DaemonSet restarting on one node, and the two PodDisruptionBudgets that match no pods...
A ResourceQuota block and a stalled billing-api rollout cleared themselves
overnight, and KubeJobFailed stopped, but both are worth watching if they
come back.

## At a glance

- Covers: since Sun 11 Oct 15:47 (24 h)
- Open findings: 4 (critical 0, warning 4, info 0)
- Overnight: 4 new, 0 resolved, 2 came and went
- Health checks: 12: 9 full, 1 unchanged (model skipped), 2 failed
- Latest check: ok
- Alerts firing now: 2
- Alerts that stopped overnight: 1

## Came and went overnight

- [warning] ResourceQuota default-ns-compute is blocking new pods for billing-api — from 12:24 to 12:27 UTC — ...

## Alerts that fired overnight and stopped

- KubeJobFailed [warning]
```

### Since the last scan

Before the report, the agent scans the cluster again with the same read-only scan as
`k8s-servant onboard` ([onboarding.md](onboarding.md)) and compares it with the scan it
stored last time. The at-a-glance list gets one line (`Since the last scan: 1 added,
1 removed, 1 changed`, or `no changes`), and the section lists each change with where
to apply it in your values files:

```
## Since the last scan

- Added: cert-manager (certificate manager) runs in namespace `cert-manager` — add the line "- cert-manager (certificate manager) runs in namespace `cert-manager`" to knowledge.environment
- Gone: Velero (backup) runs in namespace `velero` — remove it from knowledge.environment
- Changed: Node pool `user`: 2 nodes, 2× Standard_D8s_v5 → Node pool `user`: 3 nodes, 3× Standard_D8s_v5 — replace it in knowledge.clusterNotes
```

A new fact that comes with a suggested setting also names it at the end of its line,
for example `... to knowledge.clusterNotes and appConfig.PROMETHEUS_URL: http://kps-prometheus.monitoring.svc:9090`.

- The first scan is stored as a baseline and reported once
    (`First scan recorded: 13 components, 2 node pools`).
- Noise (alerts, events, restarts) is not compared: it changes every day.
- Facts from an input that could not be read this time are kept and never shown as
    gone. A failing input is listed once, when it starts failing
    (`Not checked this time: ...`); a crashed detector in every report while it lasts.
- A change the scan is unsure of is a question (`Added (unsure): ...`), not a line to
    paste. Nothing is applied for you.
- A scan that cannot read the cluster at all changes nothing in memory; the report
    says the scan failed and goes out as usual.

Details, including what the pod's RBAC cannot check:
[onboarding.md](onboarding.md#in-the-cluster).

### When it is sent

- `serve` checks every minute whether it is due: at `MORNING_REPORT_TIME` in
    `MORNING_REPORT_TIMEZONE` (summer time handled), on weekdays when
    `MORNING_REPORT_WEEKDAYS_ONLY`, and not yet sent today.
- The date it was sent is kept in memory, so a restart never sends it twice, and a pod
    that was down at 08:00 sends it as soon as it is back the same day.
- `k8s-servant report` produces one on demand. It counts as a morning report: the next
    scheduled one covers the time since it. It also re-scans and stores the scan, so the changes it
    shows are not shown again in the next scheduled report.

## Teams cards

Cards are Adaptive Cards posted to a Teams **Workflows** webhook (setup:
[MS Teams setup](teams-setup.md)).

| Card | Headline | Content |
|---|---|---|
| Health check | 🔴 / 🟠 / ✅ `my-cluster: 2 new, 0 resolved, 4 open`, time | summary; New (with details); Resolved; Still open (with age) |
| Morning report | ☀️ `Morning report — my-cluster — Mon 12 Oct 2026` | overview; the at-a-glance facts; open findings; new / resolved / came and went; since the last scan; alerts firing now and stopped overnight; new notes |

**When a health check is posted** (`TEAMS_POST_MODE`):

| Mode | Posted |
|---|---|
| `changes` (default) | something new or resolved. A second identical check, or a skipped one, posts nothing |
| `always` | every check, including failed and skipped ones |
| `off` | never (also no morning report) |

**Size.** Teams rejects cards over about 28 KB. Cards are built for at most 27,000
bytes: if too large, details are shortened step by step, then lists are cut with
"… and N more".

**The webhook URL is a credential.** It is never logged, not even in error messages,
and never written to history or cards.
