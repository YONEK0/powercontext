# SPEC-003 任务执行异常隔离（本次要实现的功能）

- 状态: active
- 类型: requirement
- 范围: schedule.Scheduler
- 目标测试: tests/test_spec_003_job_error_isolation.py
- 回归基线: tests/test_schedule_upstream.py
- 版本: v1

## R-003-001 单任务失败不得中断同轮其他任务

`Scheduler.run_pending()` 执行到期任务时，某个任务函数抛出异常，
**不得**中断同一轮中其余到期任务的执行；其余到期任务必须照常执行。

## R-003-002 失败必须可观测

任务抛出的异常必须交给 `Scheduler` 上配置的 `on_error` 回调（可调用对象），
参数为 `(job, exception)`。若未配置 `on_error`，异常必须被吞掉并记录到模块级 logger
的 `warning` 级别，且**不得**再向上抛出。

## R-003-003 on_error 配置项

`Scheduler` 必须提供 `on_error` 属性，默认值为 `None`，
允许在构造后赋值，例如 `scheduler.on_error = handler`。

## R-003-004 既有行为保持

- 正常任务仍然按 `next_run` 升序执行（见 SPEC-001 R-001-002）。
- 返回 `CancelJob` 的任务仍然被取消（见 SPEC-001 R-001-003）。
- `run_pending()` 仍然不补跑错过的周期（见 SPEC-001 R-001-001）。
- `run_all()` 同样必须隔离异常，行为与 `run_pending()` 一致。
