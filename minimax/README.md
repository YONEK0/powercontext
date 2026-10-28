# MiniMax × OceanBase — SDD 上下文模块

**规有所依——面向软件持续演进的 SDD 上下文管理系统**

本目录是 MiniMax 赛道的完整提交资产。最终提交仓库是官方
`powercontext` 的 `2026-competition-ecosystem` 分支，本目录即其中的
`minimax/` 目录。seekdb 只是存储/检索依赖，不是提交仓库。

## 模块做什么

给定一个开发任务，模块从工程资料中选出**适用的规格、需要改的代码、用于验证的测试**，
把这组带出处（路径、行号、版本、内容 hash）的上下文交给 MiniMax 生成补丁，
再用测试证明改对了，并把任务状态存进 PowerContext、重启后读回。

```
openspec/ + 业务代码 + 测试
        │  ingest
        ▼
     seekdb（文档表 + 倒排词项表）
        │  任务文本 → 分词/扩展/跨语言桥接 → SQL 检索 → 打分排序
        ▼
   开发上下文（spec/code/test + 出处 + 分数 + 命中词）
        │          │
        │          └── 探针：实跑候选测试，失败者=验收测试，通过者=回归门
        ▼
   MiniMax（OpenAI 兼容）→ unified diff → 归一化 → 应用
        │
        ▼
   目标测试 + 回归测试 → PowerContext 状态 → 重启读回
```

## 三个必须参与的技术怎么分工

| 技术 | 承担的作用 | 代码位置 |
|---|---|---|
| **MiniMax** `cn:minimax-m3.1-flash` | 根据上下文生成实际代码修改（unified diff） | `agent.py` |
| **seekdb** `pylibseekdb==1.4.0.post2` | 保存工程资料与倒排索引；检索结果**就是**上下文的唯一来源 | `db.py` / `ingest.py` |
| **PowerContext** `powercontext[server]==1.2.0` | 保存任务进展与验证结果；新进程读回并继续使用 | `state.py` |

接入方式：seekdb 用 Python 绑定直连（嵌入式）；MiniMax 用 OpenAI 兼容
`/chat/completions` HTTP；PowerContext 用 `builtin` Runtime + SQLite。

## 目录

```text
minimax/
  src/minimax_context/
    config.py     配置与 .env（占位值安全回退）
    db.py         seekdb 建表/批量导入/倒排检索/查询日志
    ingest.py     发现工程目录，切分 spec/code/test，写 seekdb
    retrieve.py   分词、IDF/BM25 打分、跨语言桥接、角色配额
    context.py    组装上下文并落盘
    task.py       从 OpenSpec change 解析任务（不写死题目）
    agent.py      MiniMax 调用、patch 提取/归一化/应用
    verify.py     探针分类、目标/回归测试执行与日志
    state.py      PowerContext 状态读写
    cli.py        命令行入口
  demo_app/       验证样例业务工程（FastAPI 图书检索）+ 数据 + 测试
  openspec/       生效规格 + add-price-filter 变更
  fixtures/inventory/   第二套对照工程（库存），用于证明泛化
  patches/recorded.patch  已录制的 MiniMax 补丁（无 Key 也能复现）
  evidence/final/ 最终一次完整运行的证据
  scripts/        init/reset/restore/run_demo
  tests/          框架自身测试
```

## 环境

- Python 3.12
- Linux x86_64（seekdb 嵌入式库只有 Linux/macOS wheel）

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 不含真实 Key；留空即用默认值
```

`.env` 全部可留空：`MINIMAX_TEST_PYTHON` 留空则复用当前解释器，
`MINIMAX_CHANGE` 留空则在只有一个 change 时自动选中，`MINIMAX_API_KEY`
留空则 `--live` 会明确报错、`--replay` 正常工作。

WSL 注意：seekdb 无法在 `/mnt/e` 上打开数据库目录，请把
`MINIMAX_SEAKDB_PATH` 指到 ext4 路径（如 `/tmp/minimax-seekdb`）。

## 一次完整验证（6 步）

`--task` / `--change` 是**全局**选项，必须写在子命令之前；不写则自动从
`openspec/changes/` 读取。

**提交的就是初始状态**：仓库里的 `demo_app/app.py` 尚未实现价格过滤，
`evidence/after/app.py` 是已验证的修改后快照。因此第 3 步的失败是真实的，
第 4 步应用补丁后即可复现第 5 步的通过。想反复演示时用
`restore before` / `restore after` 在两个快照间切换。

```bash
export PYTHONPATH=src

# 1. 建库并导入规格/代码/测试
python -m minimax_context.cli init

# 2. 构造上下文（会实跑候选测试，确定验收测试与回归门）
python -m minimax_context.cli context

# 3. 确认目标测试在改前失败（仓库初始状态即如此）
python -m minimax_context.cli restore before
python -m minimax_context.cli test --target     # 期望 exit 1

# 4. 让 MiniMax 生成并应用补丁；无 Key 时用录制补丁
python -m minimax_context.cli agent --replay --apply
# 有 Key 时：python -m minimax_context.cli agent --live --apply

# 5. 目标测试 + 回归测试
python -m minimax_context.cli test --target --regression   # 期望 exit 0

# 6. 新进程读回持久化状态
python -m minimax_context.cli status
```

预期：第 3 步 `exit_code: 1`（价格过滤未实现），第 5 步两项均 0，
第 6 步 `status: verified` 且含 `target_tests`、`regression_tests`、
两次测试的退出码与日志路径。

重置：`bash scripts/reset.sh`（清本地 seekdb/PowerContext 与生成物）。

## 上下文模块的调用入口与输入输出

入口是 CLI，也可作为库调用：

```python
from minimax_context.config import Settings
from minimax_context.db import SeekDB
from minimax_context.context import build_context

settings = Settings.load(".")
db = SeekDB(settings.seekdb_path)
ctx = build_context(settings, db, "add an inclusive price filter")
```

输入：任务文本（或 OpenSpec change 名）。
输出：`schema=minimax.sdd-context.v2`，含

- `terms`：本任务实际使用的检索词
- `groups.spec/code/test`：每组条目带 `path`、`symbol`、`line_start/end`、
  `version`、`content_hash`、`score`、`matched_terms`
- `target_files`：需要修改的代码文件
- `target_tests` / `regression_tests`：验收测试与回归门
- `probe`：确定验收测试所依据的实跑结果（命令、退出码、失败/通过列表）
- `required`：三组的 source id，便于评审核对

所有来源都真实存在于工程中；改写规格并 `cli ingest` 后，同一路径的
`version` 会变化，输出随之使用新内容（`tests/test_retrieval.py` 覆盖）。

## 泛化能力

这不是为一道题硬编码的流水线。以下三点都能在 `tests/` 里找到对照测试：

1. **分词不依赖领域词表。** 标识符按 `_`/驼峰拆分（`min_price` 可从
   `price` 命中），中文按 bigram 索引，因此中英文任务都能检索。
2. **跨语言桥接。** 任务先命中规格，再从这些规格里提取真实字段名做第二
   轮检索，所以「预约数量不能超过可用量」能命中 `reserve`/`free_quantity`。
3. **验收测试是测出来的，不是猜的。** 探针在改前代码上实跑候选测试，
   失败者为验收测试、通过者为回归门；改完后测试不再失败，所以选择结果
   存进状态复用，而不是重算。

`fixtures/inventory/` 是一套与图书检索无关的工程（不同领域、不同包名、
中文 proposal）。把它当作工程根直接运行，同一份代码会读出它自己的变更、
选中 `stock/service.py` 与对应测试，无需修改任何源码。

## 依赖版本（已知限制见 `TECH_NOTE.md`）

```text
powercontext[server]==1.2.0     # 冻结基线；1.4.1 的 Runtime 接口不同
alembic>=1.16,<2
pylibseekdb==1.4.0.post2
fastapi / uvicorn / httpx / pytest
```

## 安全

仓库不含真实 API Key、不含 `.env`。Key 只经环境变量传入。
