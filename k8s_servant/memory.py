"""The agent's memory: what it found before, what it learned, what it did.

Everything lives in one folder (DATA_DIR: the PersistentVolume at /data in the
cluster, ./.data on a laptop):

  memory.db   SQLite database with three tables:
                findings  every problem ever reported, open or resolved, keyed by
                          fingerprint (findings.py). This is what lets the next
                          check say "still open" instead of "new".
                runs      one row per health check, morning report or question,
                          with the full Markdown report (`k8s-servant history`).
                meta      small key/value facts, e.g. the date of the last morning
                          report, so a restart does not send it twice.
  notes.md    learned notes, one line each, written by the agent (save_note tool)
              or by people. The newest ones go into the system prompt.

Why SQLite: it is part of Python, needs no server, answers "what is new since last
time" with a simple query, and is one file on the volume. WAL mode and a busy
timeout let the scheduler and a `kubectl exec ... k8s-servant ask` use it at the
same time. Notes are Markdown so people can read and edit them.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from k8s_servant.findings import Finding, FindingsDiff, StoredFinding, sort_findings
from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)

# One note is one sentence; long texts belong in the environment description.
NOTE_MAX_CHARS = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    fingerprint TEXT PRIMARY KEY,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    status      TEXT NOT NULL,          -- open | resolved
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    duration_s     REAL NOT NULL,
    kind           TEXT NOT NULL,       -- check | morning | ask
    status         TEXT NOT NULL,       -- ok | failed
    summary        TEXT NOT NULL,
    report_md      TEXT NOT NULL,
    new_count      INTEGER NOT NULL DEFAULT 0,
    open_count     INTEGER NOT NULL DEFAULT 0,
    resolved_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RunRecord:
    """One recorded run (health check, morning report or question)."""

    id: int
    started_at: datetime
    duration_s: float
    kind: str  # check | morning | ask
    status: str  # ok | failed
    summary: str
    report_md: str
    new_count: int
    open_count: int
    resolved_count: int


def _ts(moment: datetime) -> str:
    """Store times as UTC ISO strings; in one format they also sort correctly as text."""
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _dt(text: str | None) -> datetime | None:
    """Parse a stored ISO timestamp; None stays None."""
    return datetime.fromisoformat(text) if text else None


def _to_finding(row: sqlite3.Row) -> StoredFinding:
    """Convert a `findings` table row into a StoredFinding."""
    return StoredFinding(
        fingerprint=row["fingerprint"],
        severity=row["severity"],
        title=row["title"],
        detail=row["detail"],
        first_seen=_dt(row["first_seen"]),
        last_seen=_dt(row["last_seen"]),
        status=row["status"],
        resolved_at=_dt(row["resolved_at"]),
    )


def _to_run(row: sqlite3.Row) -> RunRecord:
    """Convert a `runs` table row into a RunRecord."""
    return RunRecord(
        id=row["id"],
        started_at=_dt(row["started_at"]),
        duration_s=row["duration_s"],
        kind=row["kind"],
        status=row["status"],
        summary=row["summary"],
        report_md=row["report_md"],
        new_count=row["new_count"],
        open_count=row["open_count"],
        resolved_count=row["resolved_count"],
    )


class Memory:
    """Findings, runs, meta values and learned notes in DATA_DIR."""

    def __init__(self, data_dir: str | Path, notes_max_chars: int = 8000):
        """Open (and create if needed) the memory folder and database.

        Raises:
            OSError: If the folder cannot be created or written.
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "memory.db"
        self.notes_path = self.data_dir / "notes.md"
        self.notes_max_chars = notes_max_chars
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection in one transaction (commit on success, rollback on error)."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            # WAL lets a reader (e.g. `k8s-servant findings`) and the scheduler work at once.
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    # --- findings -------------------------------------------------------------

    def open_findings(self) -> list[StoredFinding]:
        """Problems that are currently open, most severe first."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM findings WHERE status = 'open'").fetchall()
        return sort_findings(map(_to_finding, rows))

    def all_findings(self) -> list[StoredFinding]:
        """Every finding ever recorded, open and resolved."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM findings").fetchall()
        return sort_findings(map(_to_finding, rows))

    def apply_check(self, current: list[Finding], now: datetime | None = None) -> FindingsDiff:
        """Store the result of a successful health check and compare it with the last one.

        Args:
            current: Findings of this check (unique fingerprints, see parse_findings).
            now: Time of the check (UTC); defaults to now.

        Returns:
            new (not open before; also problems that come back after being
            resolved), ongoing (open before and still there) and resolved (open
            before, gone now).
        """
        now = now or datetime.now(UTC)
        stamp = _ts(now)
        current_fps = [finding.fingerprint for finding in current]
        with self._connect() as conn:
            open_fps = {
                row["fingerprint"] for row in conn.execute("SELECT fingerprint FROM findings WHERE status = 'open'")
            }
            for finding in current:
                if finding.fingerprint in open_fps:
                    conn.execute(
                        "UPDATE findings SET severity = ?, title = ?, detail = ?, last_seen = ? WHERE fingerprint = ?",
                        (finding.severity, finding.title, finding.detail, stamp, finding.fingerprint),
                    )
                else:
                    # New, or seen before and resolved since: (re)open it as new.
                    conn.execute(
                        """INSERT INTO findings (fingerprint, severity, title, detail, first_seen, last_seen, status, resolved_at)
                           VALUES (?, ?, ?, ?, ?, ?, 'open', NULL)
                           ON CONFLICT(fingerprint) DO UPDATE SET
                               severity = excluded.severity, title = excluded.title, detail = excluded.detail,
                               first_seen = excluded.first_seen, last_seen = excluded.last_seen,
                               status = 'open', resolved_at = NULL""",
                        (finding.fingerprint, finding.severity, finding.title, finding.detail, stamp, stamp),
                    )
            resolved_fps = sorted(open_fps - set(current_fps))
            conn.executemany(
                "UPDATE findings SET status = 'resolved', resolved_at = ? WHERE fingerprint = ?",
                [(stamp, fingerprint) for fingerprint in resolved_fps],
            )
            rows = {row["fingerprint"]: _to_finding(row) for row in conn.execute("SELECT * FROM findings")}
        return FindingsDiff(
            new=sort_findings(rows[fp] for fp in current_fps if fp not in open_fps),
            ongoing=sort_findings(rows[fp] for fp in current_fps if fp in open_fps),
            resolved=sort_findings(rows[fp] for fp in resolved_fps),
        )

    def findings_changed_since(self, since: datetime) -> tuple[list[StoredFinding], list[StoredFinding]]:
        """(findings first seen since `since`, findings resolved since `since`)."""
        with self._connect() as conn:
            new = conn.execute("SELECT * FROM findings WHERE first_seen >= ?", (_ts(since),)).fetchall()
            resolved = conn.execute(
                "SELECT * FROM findings WHERE status = 'resolved' AND resolved_at >= ?", (_ts(since),)
            ).fetchall()
        return sort_findings(map(_to_finding, new)), sort_findings(map(_to_finding, resolved))

    # --- runs -----------------------------------------------------------------

    def record_run(
        self,
        kind: str,
        started_at: datetime,
        duration_s: float,
        status: str,
        summary: str,
        report_md: str,
        new_count: int = 0,
        open_count: int = 0,
        resolved_count: int = 0,
    ) -> int:
        """Store one run and return its id.

        Args:
            kind: `check`, `morning` or `ask`.
            started_at: When the run started (UTC).
            duration_s: How long it took.
            status: `ok` or `failed`.
            summary: One line for `k8s-servant history` (a skipped check's summary starts
                with healthcheck.SKIPPED_SUMMARY_PREFIX).
            report_md: The full Markdown report.
            new_count, open_count, resolved_count: Finding counts (checks and reports).
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO runs (started_at, duration_s, kind, status, summary, report_md,
                                     new_count, open_count, resolved_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _ts(started_at),
                    round(duration_s, 1),
                    kind,
                    status,
                    summary,
                    report_md,
                    new_count,
                    open_count,
                    resolved_count,
                ),
            )
            return cursor.lastrowid

    def runs(self, limit: int = 20) -> list[RunRecord]:
        """The most recent runs, newest first."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_to_run(row) for row in rows]

    def run(self, run_id: int) -> RunRecord | None:
        """One run by id, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _to_run(row) if row else None

    def last_run(self, kind: str, status: str = "ok") -> RunRecord | None:
        """The most recent run of a kind with the given status, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE kind = ? AND status = ? ORDER BY started_at DESC, id DESC LIMIT 1",
                (kind, status),
            ).fetchone()
        return _to_run(row) if row else None

    def runs_since(self, since: datetime) -> list[RunRecord]:
        """Runs started at or after `since`, newest first."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE started_at >= ? ORDER BY id DESC", (_ts(since),)).fetchall()
        return [_to_run(row) for row in rows]

    # --- meta -----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        """Read a value from the key/value `meta` table; None if the key was never set."""
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Store a value in the `meta` table, replacing an existing one (e.g. the last morning report date)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- learned notes ----------------------------------------------------------

    def notes(self) -> str:
        """The whole notes file ('' when there are no notes)."""
        return self.notes_path.read_text(encoding="utf-8") if self.notes_path.is_file() else ""

    def notes_for_prompt(self) -> str:
        """The newest notes that fit in notes_max_chars, cut at a line boundary."""
        text = self.notes().strip()
        if len(text) <= self.notes_max_chars:
            return text
        tail = text[-self.notes_max_chars :]
        # Start at the next full line so no note is cut in half.
        if "\n" in tail:
            tail = tail[tail.index("\n") + 1 :]
        return "(older notes omitted)\n" + tail

    def add_note(self, text: str, now: datetime | None = None) -> str:
        """Append one note as `- YYYY-MM-DD: text` and return that line.

        Raises:
            ValueError: If the note is empty or longer than NOTE_MAX_CHARS.
        """
        clean = " ".join(str(text).split())
        if not clean:
            raise ValueError("a note cannot be empty")
        if len(clean) > NOTE_MAX_CHARS:
            raise ValueError(f"a note can be at most {NOTE_MAX_CHARS} characters, got {len(clean)}")
        line = f"- {(now or datetime.now(UTC)).astimezone(UTC):%Y-%m-%d}: {clean}"
        with self.notes_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        # WARNING, not INFO: a note shapes every future run (and could be planted through
        # prompt injection in logs or events), so it must be easy to spot. New notes are
        # also listed in the morning report.
        logger.warning("New learned note (review it; it applies to all future runs): %s", clean)
        return line

    def notes_since(self, since: datetime) -> list[str]:
        """Note lines dated on or after the UTC date of `since` (notes carry a date only)."""
        cutoff = since.astimezone(UTC).date().isoformat()
        return [line for line in self.notes().splitlines() if line.startswith("- ") and line[2:12] >= cutoff]


def build_save_note_tool(memory: Memory) -> Tool:
    """Create the `save_note` tool through which the agent remembers facts."""

    async def run(arguments: dict) -> str:
        try:
            line = memory.add_note(arguments.get("note") or "")
        except ValueError as exc:
            return f"ERROR: {exc}"
        return f"Saved: {line}"

    return Tool(
        name="save_note",
        description=(
            "Remember one short fact for future runs so you do not have to rediscover it, e.g. "
            "'CronJob db-backup in namespace payments-dev fails on Sundays by design'. Save facts that stay "
            "true (what is normal here, where things are), not temporary states or findings - those are "
            "tracked separately. Never save secrets or credentials. One sentence, at most "
            f"{NOTE_MAX_CHARS} characters."
        ),
        parameters={
            "type": "object",
            "properties": {"note": {"type": "string", "description": "The fact to remember"}},
            "required": ["note"],
        },
        run=run,
        surface="memory",
    )
