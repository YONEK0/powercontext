# 来源与修改说明

## 上游来源

| 项 | 值 |
| --- | --- |
| 项目 | `schedule` —— Python 任务调度库 |
| 版本 | 1.2.2 |
| 来源 | PyPI 官方发布包 `schedule-1.2.2.tar.gz`（`schedule` 包，作者 Daniel Rodriguez，MIT 许可） |
| 许可 | MIT，许可证全文见 `LICENSE.schedule.txt` |
| 作者名单 | `AUTHORS.schedule.rst` |

## 本仓库对上游的修改

| 文件 | 修改 |
| --- | --- |
| `schedule/__init__.py` | 在 `Scheduler` 上新增 `on_error` 回调与 `job_failures` 列表；`Scheduler._run_job` 改为隔离单个任务异常 |
| `tests/test_schedule_upstream.py` | **未做任何修改**，是 schedule 1.2.2 的原样官方测试（81 个用例） |

修改后的完整差异见 `../evidence/07-代码差异-阶段1.patch` 与 `../evidence/11-代码差异-阶段2.patch`；
修改前的原始文件保存在 `upstream_reference/schedule__init__v1.2.2.py`。

## 新增文件（不属于上游）

| 文件 | 说明 |
| --- | --- |
| `specs/` | 本次任务的规格条目（SDD 规格，按 `## R-xxx-xxx` 组织） |
| `spec_revisions/` | 规格的历史版本，用于演示"规格升级后重新导入" |
| `tests/test_spec_003_job_error_isolation.py` | SPEC-003 v1 目标测试 |
| `tests/test_spec_003_v2_hardening.py` | SPEC-003 v2 目标测试 |
| `tests/test_spec_003_preserved_behaviour.py` | 既有行为回归测试 |

## 本目录的运行方式

```bash
cd business_project
python -m pytest tests/ -v
```

只依赖 `pytest` 与 `pytz`（官方时区用例需要 `pytz`；未安装时这些用例会 skip）。
