# MiniMax × OceanBase —— 规有所依：面向软件持续演进的 SDD 上下文模块

为 AI 开发助手实现一个**工程上下文模块**：根据开发任务找到适用的规格、相关代码和测试，
保存任务进展，并通过一次真实的代码修改验证模块的作用。

三项技术各自承担明确的职责：

| 技术 | 角色 | 落点 |
| --- | --- | --- |
| **seekdb / OceanBase** | 存储并检索工程资料（规格 / 代码 / 测试） | `sdctx/seekdb_store.py`、`sdctx/retrieve.py` |
| **MiniMax** | 参与本次任务的代码生成与修改 | `sdctx/minimax_client.py`、`sdctx/agent.py` |
| **PowerContext** | 保存任务进展与验证结果，并在后续运行中读取复用 | `sdctx/state.py`、`sdctx/powercontext_client.py` |

## 快速开始

```bash
cd minimax
bash scripts/setup_env.sh && source .venv/bin/activate
export MINIMAX_API_KEY=replace-with-your-minimax-api-key

bash scripts/start_powercontext.sh &     # 任务状态服务
bash scripts/run_validation.sh           # 跑完整验证样例，产物写入 evidence/
```

详见 [`docs/01-运行说明.md`](docs/01-运行说明.md)。

## 命令行入口

```bash
python -m sdctx init                    # 建表 + 全文索引 + 创建/复用 PowerContext Scope
python -m sdctx ingest                  # 导入规格 / 代码 / 测试到 seekdb
python -m sdctx context --task "..."    # 检索并组装开发上下文
python -m sdctx state save --task "..." # 保存任务状态到 PowerContext
python -m sdctx state load --task-id .. # 新进程读回任务状态
python -m sdctx patch   --task "..."    # 用 MiniMax 生成并落地代码修改（自带测试回环）
python -m sdctx search  --query "..."   # 直接在 seekdb 里检索
python -m sdctx sql     --query "..."   # 直接执行只读 SQL
python -m sdctx doctor                  # 环境体检
```

## 目录

| 路径 | 说明 |
| --- | --- |
| `sdctx/` | 上下文模块源码 |
| `business_project/` | 被修改的业务工程（schedule 1.2.2，MIT）与规格、测试 |
| `tests/test_sdctx.py` | 上下文模块自身的单元测试 |
| `scripts/` | `setup_env.sh` / `start_powercontext.sh` / `reset_demo.sh` / `run_validation.sh` |
| `docs/01-运行说明.md` | 依赖、配置步骤、调用入口、测试命令、已知限制 |
| `docs/02-验证样例.md` | 任务描述、有效规格、初始数据、操作步骤、预期结果、重置方法 |
| `docs/03-技术使用记录.md` | MiniMax 开发记录、数据库查询记录、PowerContext 状态写入与复用记录 |
| `docs/04-一页技术说明.md` | 模块结构、上下文选择方式、各项技术的分工 |
| `evidence/` | 全部运行证据（测试日志、代码差异、模型调用记录、数据库查询、状态记录） |

## 验收对照

| 必做项 | 本作品的做法 | 证据 |
| --- | --- | --- |
| 能够运行 | `scripts/run_validation.sh` 从零跑通全流程 | `evidence/00-环境体检.json` |
| MiniMax 实际参与 | 模型产出符号替换块并真实落盘，调用全过程留档 | `evidence/06-*.json`、`evidence/12-*.json` |
| 上下文正确输出 | 规格条目 / 代码符号 / 测试用例均带真实来源；规格升级后输出随之变化 | `evidence/03-*.json`、`evidence/10-*.json` |
| 数据库实际参与 | 资料存进 seekdb，检索 SQL 的返回行直接构成上下文 | `evidence/04-seekdb-查询记录.txt` |
| 状态能够复用 | 状态写进 PowerContext；新进程读回并注入上下文 | `evidence/05-powercontext-状态记录.txt` |
| 代码修改有效 | 同一组目标测试修改前 5 失败 / 修改后 5 通过，回归测试全通过 | `evidence/01`、`02`、`08`、`09`、`13` |
