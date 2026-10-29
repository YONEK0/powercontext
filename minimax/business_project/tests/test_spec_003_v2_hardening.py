"""SPEC-003（v2）目标测试：错误上报的加固要求。

覆盖规格条目：

* R-003-002（v2 追加）失败必须计入 ``Scheduler.job_failures``
* R-003-005 on_error 回调自身抛异常时不得影响其余任务

这些要求在规格从 v1 升级到 v2 之后才生效，因此它们在 v1 实现下仍然失败，
在 v2 实现下必须全部通过。
"""

from __future__ import annotations

import datetime
import logging

import pytest

import schedule


def _make_due(scheduler: "schedule.Scheduler", func, interval: int = 1):
    job = scheduler.every(interval).seconds.do(func)
    job.next_run = datetime.datetime.now() - datetime.timedelta(seconds=1)
    return job


def _boom():
    raise RuntimeError("job exploded")


def test_on_error_handler_itself_failure_does_not_break_other_jobs():
    """R-003-005: on_error 回调自身抛异常时不得影响其余任务，并记录 warning。"""

    scheduler = schedule.Scheduler()
    ran = []
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    def bad_handler(job, exception):
        raise ValueError("handler exploded")

    logger = logging.getLogger("schedule")
    handler = _Capture()
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        scheduler.on_error = bad_handler
        _make_due(scheduler, _boom)
        _make_due(scheduler, lambda: ran.append("second"))

        scheduler.run_pending()  # 不应抛出
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert ran == ["second"]
    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert warnings, "on_error 回调自身失败必须记录一条 warning 日志"
    assert "handler exploded" in warnings[0].getMessage()


def test_job_failures_records_failures():
    """R-003-002（v2）: 失败必须计入 Scheduler.job_failures。"""

    scheduler = schedule.Scheduler()
    failing = _make_due(scheduler, _boom)
    _make_due(scheduler, lambda: None)

    scheduler.run_pending()

    assert hasattr(scheduler, "job_failures")
    assert len(scheduler.job_failures) == 1
    recorded_job, recorded_exception = scheduler.job_failures[0]
    assert recorded_job is failing
    assert isinstance(recorded_exception, RuntimeError)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
