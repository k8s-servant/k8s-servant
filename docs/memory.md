# Memory

The model behind the agent remembers nothing between requests. So that the agent can
say "this problem is still open since yesterday" instead of reporting the same thing
every hour, the agent keeps its own memory on disk. This page explains what is
stored, how it is used, and how to look at it or reset it.

## Where it lives

Everything is in one folder, `DATA_DIR`:

| Where | `DATA_DIR` |
|---|---|
| Laptop | `.data` in the current directory (the default) |
| Cluster | `/data`, a small PersistentVolume (1 Gi) mounted into the agent pod |

```
DATA_DIR/
├── memory.db    SQLite database: findings, runs, meta
└── notes.md     learned notes, one line each
```

SQLite was chosen because:
- it is part of Python and needs no server;
- "what is new since last time" is a simple query;
- the whole memory is one file on the volume.

`notes.md` is plain Markdown so that people can read and edit it.

## Findings: new, still open, resolved

A **finding** is one problem a health check reported, such as "Deployment `api` in
`payments-dev` is crash-looping". Every finding has a **fingerprint**, a stable
identity built from *what* is broken, not *when* or *how* it was seen:

```
<category>/<namespace, or _cluster>/<Kind>/<name>
crashloop/payments-dev/deployment/api
node-not-ready/_cluster/node/aks-nodepool1-12345678-vmss000003
```

Fingerprints point at the owning workload (Deployment, StatefulSet, …), not the pod,
so a recreated pod keeps the same fingerprint. They are normalised (lower case, no
spaces) because the model will not always spell them identically. The list of
categories is fixed in `healthcheck.CATEGORIES`.

After every successful check, `Memory.apply_check()` compares the reported
fingerprints with the findings that were open before:

| Result | Meaning | What happens in memory |
|---|---|---|
| **new** | Not open before, including a problem that comes back after being resolved | Inserted (or reopened) with `first_seen` = now |
| **still open** | Open before and reported again | `last_seen`, severity, title and detail updated |
| **resolved** | Open before, not reported now | `status = resolved`, `resolved_at` = now |

Example over three hourly checks:

| Check | Reported | new | still open | resolved | Teams (mode `changes`) |
|---|---|---|---|---|---|
| 09:00 | A, B | A, B | – | – | posted |
| 10:00 | A, B | – | A, B | – | **not posted**: nothing changed |
| 11:00 | A | – | A | B | posted ("B resolved") |

To help the model reuse fingerprints, every check starts with the list of findings
that are currently open, and the model is asked to re-verify each of them.

### A failed check never changes memory

The model ends its answer with a JSON block of findings. If that block is missing or
broken, the model gets one follow-up asking for just the JSON. If the second answer
is no better, the run is stored as **failed** and the findings are **not touched**.

Otherwise a broken answer, which contains no findings, would mark every open problem
as resolved and post a misleading "all resolved" message. The same applies when the
model cannot be reached at all.

## Runs: the history

Every health check, morning report and question (`ask`) is stored as a run:

| Field | Content |
|---|---|
| `id`, `started_at`, `duration_s` | When it ran and for how long |
| `kind` | `check`, `morning` or `ask` |
| (skipped checks) | a scheduled check that found nothing changed is a `check` run whose summary starts with "Unchanged since the full check at ..." ([reports.md](reports.md#when-the-model-is-skipped)) |
| `status` | `ok` or `failed` |
| `summary` | One line: the check's summary, the morning overview, or the question |
| `report_md` | The full Markdown report (or the question and answer) |
| counts | new / open / resolved, for checks and morning reports |

## Meta

A small key/value table for facts the scheduler needs across restarts. Today there is
one: `last_morning_report_date`, the date the morning report was last sent, in the
report's time zone. Because of it, a pod restarted after 08:00 does not send the
morning report twice, and a pod that was down at 08:00 sends it as soon as it is
back the same day.

## Learned notes

Notes are short facts the agent should not have to rediscover, for example:

```
- 2026-10-12: CronJob db-backup in payments-dev fails on Sundays by design (source DB is stopped)
- 2026-10-13: Deployment legacy-api in shop-dev runs with 0 replicas on purpose
```

- **Who writes them.** The agent writes them with its `save_note` tool when it learns a
  lasting fact during an investigation. People can add or remove lines too.
- **Size limits.** One sentence, at most 500 characters per note. Only the newest notes
  up to `NOTES_MAX_CHARS` (default 8000 characters) go into the system prompt, so a
  growing file cannot crowd out the investigation. Older notes are skipped, with a
  marker.
- **What does not belong in a note.** Secrets, and temporary states. Findings are
  tracked separately.
- **Every new note is visible.** It is logged at WARNING, and the morning report has a
  section **"Notes saved overnight"**. A note changes what every future run
  treats as normal, and it could be planted by text in logs or events (prompt
  injection), so somebody should look at new notes and delete wrong ones.
- **Notes vs the environment description.** Notes are the agent's scratchpad.
  Facts that stay true and matter to everyone should be promoted into the
  environment description or cluster notes in the values files, which are
  reviewed through Git (see [knowledge-and-skills.md](knowledge-and-skills.md)).
  After promoting a note, delete it from `notes.md`.

**Editing notes in the cluster.** The agent's container has no editor:

```bash
kubectl -n k8s-servant cp k8s-servant-<pod>:/data/notes.md ./notes.md -c agent
# edit ./notes.md
kubectl -n k8s-servant cp ./notes.md k8s-servant-<pod>:/data/notes.md -c agent
```

## Looking at memory

```console
$ k8s-servant findings            # open findings, most severe first
critical open                         3 h 5 min  crashloop/payments-dev/deployment/api  api crash-loops
warning  open                       2 d 1 h      quota/shop-dev/resourcequota/default-ns-compute  ResourceQuota full

$ k8s-servant findings --all      # including resolved ones
$ k8s-servant history             # recent runs
   42  2026-10-12 06:00  check  ok     new 1   open 2   resolved 0   One new crash loop in payments-dev.
   41  2026-10-12 05:00  morning ok     new 0   open 1   resolved 1   Mostly healthy; ...
$ k8s-servant history 42          # the full report of run 42
$ k8s-servant notes               # learned notes
```

In the cluster, prefix these with
`kubectl -n k8s-servant exec -it deploy/k8s-servant -c agent --`. The commands use
the same memory as the scheduler.

## Concurrency

The scheduler and a person running `k8s-servant` in the same pod can use the
database at the same time. SQLite runs in WAL mode, which lets readers and a writer
work together, and every connection waits up to 30 seconds for a lock. Each
operation is one short transaction.

## Backup, loss and reset

- **Losing the volume loses only history and notes.** The next health check sees the
  cluster as it is and reports the current problems as new again. That is one noisy
  Teams post, nothing worse.
- **To reset memory**, delete `memory.db` (and `notes.md` for the notes) and let the
  agent start fresh. In the cluster, delete them in the pod or recreate the PVC.
- No backup is configured by default. If the history becomes valuable, add the PVC
  to the cluster's backup.
