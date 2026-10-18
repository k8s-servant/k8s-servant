"""The scheduler behind `k8s-servant serve`, the agent's main process in the cluster.

Every loop (at least once a minute):
  1. touch the heartbeat file (the pod's liveness probe checks that it is fresh);
  2. if the morning report is due, run it (it includes a fresh health check, so the
     next regular check is counted from then);
  3. otherwise, if the next health check is due, run it;
  4. sleep until the next check, at most 60 seconds, or until asked to stop.

One job runs at a time. A failing job is logged and the loop keeps going: one bad
run (model timeout, network glitch) must not take the agent down. A model error is
logged by type only, never with a traceback, because provider error bodies can echo
the API key (the jobs log the redacted explanation themselves). While a job runs:
  - the heartbeat keeps being touched by a small background task, so the liveness
    probe sees a living process, not a busy loop;
  - a job that exceeds JOB_TIMEOUT_MINUTES is cancelled and logged;
  - SIGTERM (pod shutdown) cancels the job at once, so the pod stops within its
    grace period instead of being killed mid-run.
The day of the morning report is marked before it runs, so a report that keeps
failing (or is cut off by a shutdown) is not retried every minute; that day's
report is then lost, and the failure is in the log.

The jobs are passed in by cli.py; that keeps this module small and lets the tests
drive it with a fake clock.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from k8s_servant.llm.errors import MODEL_ERRORS
from k8s_servant.memory import Memory
from k8s_servant.report import LAST_REPORT_KEY, local_date, report_due

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep for `seconds`, or less if `stop` is set meanwhile."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


class Scheduler:
    """Runs the health check every CHECK_INTERVAL_MINUTES and the morning report when due."""

    def __init__(
        self,
        config: dict,
        memory: Memory,
        check_job: Job,
        morning_job: Job,
        clock: Callable[[], datetime] | None = None,
        wait: Callable[[asyncio.Event, float], Awaitable[None]] | None = None,
        job_timeout_seconds: float | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ):
        """Create the scheduler.

        Args:
            config: Settings (check interval, job timeout, morning report, heartbeat file).
            memory: The agent's memory (the date of the last morning report).
            check_job: Runs one health check (cli.check_and_post).
            morning_job: Runs the morning report (cli.report_and_post).
            clock: Current time; injectable for tests.
            wait: Sleeps until the stop event or the timeout; injectable for tests.
            job_timeout_seconds: Cancel a job running longer than this
                (default JOB_TIMEOUT_MINUTES).
            heartbeat_interval_seconds: How often the heartbeat is touched while a job runs.
        """
        self.config = config
        self.memory = memory
        self.check_job = check_job
        self.morning_job = morning_job
        self.clock = clock or (lambda: datetime.now(UTC))
        self.wait = wait or wait_or_stop
        self.interval = timedelta(minutes=config["check_interval_minutes"])
        self.heartbeat_file = config["heartbeat_file"]
        self.job_timeout_seconds = job_timeout_seconds or config["job_timeout_minutes"] * 60
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Loop until `stop` is set."""
        logger.info(
            "Scheduler started: health check every %d min; morning report %s",
            self.config["check_interval_minutes"],
            f"at {self.config['morning_report_time']} {self.config['morning_report_timezone']}"
            + (" on weekdays" if self.config["morning_report_weekdays_only"] else "")
            if self.config["morning_report_enabled"]
            else "disabled",
        )
        next_check = self.clock()  # check right away on start
        while not stop.is_set():
            self._heartbeat()
            now = self.clock()
            if report_due(now, self.config, self.memory.get_meta(LAST_REPORT_KEY)):
                # Mark the day first: a report that keeps failing must not be retried every minute.
                self.memory.set_meta(LAST_REPORT_KEY, local_date(now, self.config))
                await self._run_job("morning report", self.morning_job, stop)
                next_check = self.clock() + self.interval
            elif now >= next_check:
                await self._run_job("health check", self.check_job, stop)
                next_check = self.clock() + self.interval
            seconds = min(60.0, max(1.0, (next_check - self.clock()).total_seconds()))
            await self.wait(stop, seconds)
        logger.info("Scheduler stopped")

    def _heartbeat(self) -> None:
        """Touch HEARTBEAT_FILE for the liveness probe; a failure is logged, never raised."""
        if not self.heartbeat_file:
            return
        try:
            Path(self.heartbeat_file).touch()
        except OSError as exc:
            logger.warning("Cannot touch heartbeat file %s: %s", self.heartbeat_file, exc.strerror)

    async def _beat_while_running(self) -> None:
        """Touch the heartbeat periodically while a job runs (cancelled when it ends)."""
        while True:
            self._heartbeat()
            await asyncio.sleep(self.heartbeat_interval_seconds)

    async def _run_job(self, name: str, job: Job, stop: asyncio.Event) -> None:
        """Run one job; never raises. Ends early on stop or after the job timeout."""
        job_task = asyncio.create_task(job())
        stop_task = asyncio.create_task(stop.wait())
        beat_task = asyncio.create_task(self._beat_while_running())
        try:
            done, _ = await asyncio.wait(
                {job_task, stop_task}, timeout=self.job_timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if job_task in done:
                error = job_task.exception()
                if error is not None and _involves_model_error(error):
                    # The jobs log model errors redacted; a traceback would print the
                    # provider's body, which may echo the API key, so name the type only.
                    logger.error("The %s failed (%s); the scheduler keeps running", name, type(error).__name__)
                elif error is not None:
                    logger.error("The %s failed; the scheduler keeps running", name, exc_info=error)
            elif stop_task in done:
                logger.info("Stop requested: cancelling the running %s", name)
            else:
                logger.error("The %s did not finish within %.0f s and was cancelled", name, self.job_timeout_seconds)
        finally:
            for task in (job_task, stop_task, beat_task):
                task.cancel()
            await asyncio.gather(job_task, stop_task, beat_task, return_exceptions=True)


def _involves_model_error(error: BaseException) -> bool:
    """Whether `error`, or an exception it was raised from or during, is a model-call failure."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, MODEL_ERRORS):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False
