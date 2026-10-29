# SPEC-001 调度器核心行为（既有行为，必须保持）

- 状态: active
- 类型: constraint
- 范围: schedule.Scheduler / schedule.Job
- 回归基线: tests/test_schedule_upstream.py

## R-001-001 周期调度语义

`Scheduler.every(interval)` 创建的 `Job` 必须按其 `unit` 与 `interval` 周期触发。
`Scheduler.run_pending()` 只运行 `should_run` 为真的任务，且**不重复补跑**错过的周期。

## R-001-002 执行顺序

`Scheduler.run_pending()` 必须按任务的 `next_run` 时间**升序**执行所有到期任务。

## R-001-003 返回 CancelJob 即取消

任务函数返回 `CancelJob` 实例或 `CancelJob` 类本身时，`Scheduler` 必须取消该任务，
被取消的任务不得再出现在 `get_jobs()` 结果中。

## R-001-004 run_all 语义

`Scheduler.run_all(delay_seconds)` 必须无视是否到期，执行**全部**任务，并在相邻任务之间
等待 `delay_seconds` 秒。

## R-001-005 标签筛选

`Job.tag(*tags)` 为任务附加标签；`Scheduler.get_jobs(tag=None)` 返回全部任务，
传入标签时只返回包含该标签的任务；`Scheduler.clear(tag=None)` 同理只清除匹配标签的任务。

## R-001-006 at() 时间解析

`Job.at("HH:MM")` 与 `Job.at("HH:MM:SS")` 必须正确解析本地时间；
非法时间必须抛出 `ScheduleValueError`。
