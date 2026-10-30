# business_project —— 被本次任务修改的业务工程

这是本次验证样例使用的业务工程：**schedule 1.2.2**（MIT，来源见 `NOTICE.md`）。

```
business_project/
├── schedule/                     上游源码（本次被修改）
├── upstream_reference/           修改前的原始快照
├── specs/                        当前生效的规格（SDD）
├── spec_revisions/               规格历史版本（v1 / v2）
└── tests/
    ├── test_schedule_upstream.py        上游官方测试（原样保留）
    ├── test_spec_003_job_error_isolation.py   SPEC-003 v1 目标测试
    ├── test_spec_003_v2_hardening.py           SPEC-003 v2 目标测试
    └── test_spec_003_preserved_behaviour.py    既有行为回归测试
```

运行：

```bash
python -m pytest tests/ -v
```

规格与测试的对应关系、预期结果与重置方法见 `../docs/02-验证样例.md`。
