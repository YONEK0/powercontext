"""既有行为回归测试（SPEC-001 / SPEC-002）。

这些测试在修改前必须通过，在修改后必须仍然通过，
用于证明"新增能力没有破坏原有行为"。
"""

from __future__ import annotations

import datetime

import pytest

import schedule


def _make_due(scheduler: "schedule.Scheduler", func, interval: int = 1):
    job = scheduler.every(interval).seconds.do(func)
    job.next_run = datetime.datetime.now() - datetime.timedelta(seconds=1)
    return job


def test_due_jobs_run_in_next_run_order():
    """R-001-002: 正常任务按 next_run 升序执行。"""

    scheduler = schedule.Scheduler()
    order = []

    first = scheduler.every().seconds.do(lambda: order.append("first"))
    second = scheduler.every().seconds.do(lambda: order.append("second"))
    first.next_run = datetime.datetime.now() - datetime.timedelta(seconds=5)
    second.next_run = datetime.datetime.now() - datetime.timedelta(seconds=1)

    scheduler.run_pending()

    assert order == ["first", "second"]


def test_cancel_job_behaviour_is_preserved():
    """R-001-003: 返回 CancelJob 的任务仍然被取消。"""

    scheduler = schedule.Scheduler()

    def canceller():
        return schedule.CancelJob

    job = _make_due(scheduler, canceller)
    assert job in scheduler.get_jobs()

    scheduler.run_pending()

    assert job not in scheduler.get_jobs()


def test_missed_jobs_are_not_repeated():
    """R-001-001: run_pending() 不补跑错过的周期。"""

    scheduler = schedule.Scheduler()
    calls = []
    job = scheduler.every().seconds.do(lambda: calls.append(1))
    job.next_run = datetime.datetime.now() - datetime.timedelta(seconds=1)

    scheduler.run_pending()
    assert calls == [1]

    scheduler.run_pending()
    assert calls == [1]


def test_tag_filtering_is_preserved():
    """R-001-005: get_jobs(tag=...) 仍然按标签筛选。"""

    scheduler = schedule.Scheduler()
    scheduler.every().seconds.do(lambda: None).tag("alpha")
    scheduler.every().seconds.do(lambda: None).tag("beta")

    assert len(scheduler.get_jobs()) == 2
    assert len(scheduler.get_jobs(tag="alpha")) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
