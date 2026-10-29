# SPEC-002 next_run 与 idle_seconds（既有行为，必须保持）

- 状态: active
- 类型: constraint
- 范围: schedule.Scheduler
- 回归基线: tests/test_schedule_upstream.py

## R-002-001 next_run 取最早任务

`Scheduler.next_run`（即 `get_next_run()`）必须返回所有任务中最早的 `next_run` 时间；
没有任务时返回 `None`。传入 `tag` 时只在匹配该标签的任务中取最早值。

## R-002-002 idle_seconds 计算

`Scheduler.idle_seconds` 必须返回距离下一次运行还有多少秒；
当没有任务或没有下次运行时返回 `None`。

## R-002-003 模块级快捷函数

模块级 `get_jobs` / `clear` / `cancel_job` / `next_run` / `idle_seconds` / `every`
必须等价于默认 `Scheduler` 实例上的同名方法。
