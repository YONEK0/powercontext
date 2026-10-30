"""SPEC-003（v1）目标测试：任务执行异常隔离。

覆盖规格条目：

* R-003-001 单任务失败不得中断同轮其他任务
* R-003-002 失败必须可观测（on_error 回调 / logger warning）
* R-003-003 on_error 配置项

修改前：这些测试因为"功能未实现"而失败（任务异常直接抛给调用方）。
修改后：这些测试必须全部通过。
"""

from __future__ import annotations

import datetime
import logging

import pytest

import schedule


def _make_due(scheduler: "schedule.Scheduler", func, interval: int = 1):
    """注册一个任务并把它变成"已经到期"。"""

    job = scheduler.every(interval).seconds.do(func)
    job.next_run = datetime.datetime.now() - datetime.timedelta(seconds=1)
    return job


def _boom():
    raise RuntimeError("job exploded")


def test_failing_job_does_not_block_other_due_jobs():
    """R-003-001: 一个任务失败时，同轮其余到期任务仍要执行。"""

    scheduler = schedule.Scheduler()
    ran = []
    _make_due(scheduler, _boom)
    _make_due(scheduler, lambda: ran.append("second"))

    scheduler.run_pending()  # 不应把异常抛给调用方

    assert ran == ["second"]


def test_failing_job_does_not_block_other_due_jobs_in_run_all():
    """R-003-004: run_all() 与 run_pending() 的隔离行为一致。"""

    scheduler = schedule.Scheduler()
    ran = []
    _make_due(scheduler, _boom)
    _make_due(scheduler, lambda: ran.append("second"))

    scheduler.run_all(delay_seconds=0)

    assert ran == ["second"]


def test_on_error_handler_receives_job_and_exception():
    """R-003-002: 异常必须交给 on_error 回调，参数为 (job, exception)。"""

    scheduler = schedule.Scheduler()
    seen = []

    def handler(job, exception):
        seen.append((job, exception))

    scheduler.on_error = handler
    failing = _make_due(scheduler, _boom)
    _make_due(scheduler, lambda: None)

    scheduler.run_pending()

    assert len(seen) == 1
    reported_job, reported_exception = seen[0]
    assert reported_job is failing
    assert isinstance(reported_exception, RuntimeError)
    assert str(reported_exception) == "job exploded"


def test_failure_is_logged_when_no_on_error_handler():
    """R-003-002: 未配置 on_error 时异常被记录为 warning 且不再抛出。"""

    scheduler = schedule.Scheduler()
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("schedule")
    handler = _Capture()
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        _make_due(scheduler, _boom)
        scheduler.run_pending()  # 不应抛出
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert warnings, "失败任务必须记录一条 warning 日志"
    assert "job exploded" in warnings[0].getMessage()


def test_on_error_attribute_defaults_to_none():
    """R-003-003: Scheduler.on_error 默认存在且为 None。"""

    scheduler = schedule.Scheduler()
    assert scheduler.on_error is None

    def handler(job, exception):
        return None

    scheduler.on_error = handler
    assert scheduler.on_error is handler


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
