import asyncio
from datetime import UTC, datetime, timedelta

from k8s_servant.config import configure_app
from k8s_servant.memory import Memory
from k8s_servant.report import LAST_REPORT_KEY
from k8s_servant.scheduler import Scheduler, wait_or_stop


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def _harness(tmp_path, start, run_for, env=None, check_error=None):
    """A scheduler with a fake clock that stops after `run_for`; returns (scheduler, clock, calls, memory)."""
    clock = FakeClock(start)
    memory = Memory(tmp_path / "data")
    calls = []

    async def check_job():
        calls.append(("check", clock.now))
        if check_error:
            raise check_error

    async def morning_job():
        calls.append(("morning", clock.now))

    end = start + run_for

    async def wait(stop, seconds):
        clock.now += timedelta(seconds=seconds)
        if clock.now >= end:
            stop.set()

    config = configure_app(
        {"HEARTBEAT_FILE": str(tmp_path / "heartbeat"), "MORNING_REPORT_TIMEZONE": "Europe/Vilnius", **(env or {})}
    )
    return Scheduler(config, memory, check_job, morning_job, clock=clock, wait=wait), clock, calls, memory


SATURDAY_10 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # no morning report on Saturdays
MONDAY_04 = datetime(2026, 10, 12, 4, 0, tzinfo=UTC)  # 07:00 in Vilnius


async def test_check_runs_on_start_and_then_every_interval(tmp_path):
    scheduler, _, calls, _ = _harness(tmp_path, SATURDAY_10, timedelta(hours=2, minutes=30))
    await scheduler.run(asyncio.Event())
    assert calls == [
        ("check", SATURDAY_10),
        ("check", SATURDAY_10 + timedelta(hours=1)),
        ("check", SATURDAY_10 + timedelta(hours=2)),
    ]
    assert (tmp_path / "heartbeat").exists()


async def test_morning_report_at_0800_local_replaces_that_hours_check(tmp_path):
    scheduler, _, calls, memory = _harness(tmp_path, MONDAY_04, timedelta(hours=2))
    await scheduler.run(asyncio.Event())
    assert calls == [("check", MONDAY_04), ("morning", MONDAY_04 + timedelta(hours=1))]
    assert memory.get_meta(LAST_REPORT_KEY) == "2026-10-12"


async def test_restart_on_the_same_day_does_not_send_the_report_again(tmp_path):
    first, _, _, _ = _harness(tmp_path, MONDAY_04, timedelta(hours=2))
    await first.run(asyncio.Event())
    second, _, calls, _ = _harness(tmp_path, MONDAY_04 + timedelta(hours=3), timedelta(hours=2))
    await second.run(asyncio.Event())
    assert [kind for kind, _ in calls] == ["check", "check"]


async def test_a_failing_job_does_not_stop_the_scheduler(tmp_path):
    scheduler, _, calls, _ = _harness(
        tmp_path, SATURDAY_10, timedelta(hours=1, minutes=30), check_error=RuntimeError("model down")
    )
    await scheduler.run(asyncio.Event())
    assert [kind for kind, _ in calls] == ["check", "check"]


async def test_a_model_error_that_escapes_a_job_is_logged_without_its_text(tmp_path, caplog):
    # cli.serve_jobs logs model errors redacted; should one still escape, the provider's
    # body (which may echo the API key) must not reach the log through a traceback.
    import httpx2
    import openai

    response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    error = openai.AuthenticationError("Incorrect API key provided: sk-test-secret.", response=response, body=None)
    scheduler, _, _, _ = _harness(tmp_path, SATURDAY_10, timedelta(minutes=30), check_error=error)
    await scheduler.run(asyncio.Event())
    assert "sk-test-secret" not in caplog.text
    assert "AuthenticationError" in caplog.text


async def test_wait_or_stop_returns_early_when_stopped():
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(wait_or_stop(stop, 3600), timeout=1)


async def test_a_job_that_hangs_is_cancelled_after_the_job_timeout(tmp_path):
    # Final review: without a limit one stuck check blocks the loop (and the heartbeat) for hours.
    clock = FakeClock(SATURDAY_10)
    calls = []

    async def hanging_check():
        calls.append("start")
        await asyncio.sleep(3600)

    async def wait(stop, seconds):
        clock.now += timedelta(seconds=seconds)
        if len(calls) >= 2:
            stop.set()

    config = configure_app({"CHECK_INTERVAL_MINUTES": "1"})
    scheduler = Scheduler(
        config, Memory(tmp_path), hanging_check, hanging_check, clock=clock, wait=wait, job_timeout_seconds=0.2
    )
    await asyncio.wait_for(scheduler.run(asyncio.Event()), timeout=5)
    assert calls == ["start", "start"]  # the loop went on after the first check timed out


async def test_stop_cancels_a_running_job(tmp_path):
    # Final review: SIGTERM must not wait for a long job (Kubernetes kills the pod after its grace period).
    started = asyncio.Event()

    async def long_check():
        started.set()
        await asyncio.sleep(3600)

    async def noop():
        pass

    stop = asyncio.Event()
    scheduler = Scheduler(configure_app({}), Memory(tmp_path), long_check, noop, clock=lambda: SATURDAY_10)
    runner = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(started.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(runner, timeout=5)


async def test_heartbeat_is_touched_while_a_job_runs(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    seen = []

    async def slow_check():
        heartbeat.unlink(missing_ok=True)
        await asyncio.sleep(0.3)
        seen.append(heartbeat.exists())

    async def wait(stop, seconds):
        stop.set()

    config = configure_app({"HEARTBEAT_FILE": str(heartbeat)})
    scheduler = Scheduler(
        config,
        Memory(tmp_path / "m"),
        slow_check,
        slow_check,
        clock=lambda: SATURDAY_10,
        wait=wait,
        heartbeat_interval_seconds=0.05,
    )
    await scheduler.run(asyncio.Event())
    assert seen == [True]


def test_job_timeout_setting():
    assert configure_app({})["job_timeout_minutes"] == 30
