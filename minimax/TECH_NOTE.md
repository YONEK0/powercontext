# 一页技术说明

## 模块结构

| 层 | 文件 | 职责 |
|---|---|---|
| 存储 | `db.py` | seekdb 建表、批量导入、倒排词项检索、SQL 日志 |
| 摄取 | `ingest.py` | 发现工程目录，按 AST 切分文件/函数/测试，标注 spec 角色 |
| 选择 | `retrieve.py` | 分词、扩展、跨语言桥接、IDF+BM25 打分、角色配额 |
| 组装 | `context.py` | 生成带出处的上下文，运行探针确定验收测试 |
| 任务 | `task.py` | 从 OpenSpec change 解析任务文本与需求条目 |
| 生成 | `agent.py` | MiniMax 调用、diff 归一化、可靠应用 |
| 验证 | `verify.py` | 候选测试探针、目标/回归测试执行、日志与代码版本 |
| 状态 | `state.py` | PowerContext 读写、事件历史、跨进程复用 |

## 上下文选择方式

**不写死题目，也不写死关键词表。** 选择分四步：

1. **语料驱动分词**：文本 → 标识符整体 + `_`/驼峰拆分部分 + CJK bigram。
   `min_price` 可被 `price` 命中，`为图书检索增加价格区间` 可被 `价格`、`过滤` 命中。
2. **两级检索**：任务词先查（IDF 加权），再从命中的规格里提取真实词汇做
   第二轮（权重 ×0.6）。这让中文任务能命中英文代码，而不需要翻译表或向量模型。
3. **打分**：`IDF × BM25 长度归一化 × 类型先验 × 规格角色权重`，对符号名与
   路径命中加分。长度归一化是关键——否则冗长的 `design.md` 会盖过写明了
   规则的短 `spec.md`。
4. **角色配额**：OpenSpec 里 `changes/*/specs/*/spec.md` 是规范性的变更定义，
   `proposal/design/tasks.md` 是过程文档。每类给配额，保证变更定义不被
   过程文档挤出。

**验收测试与回归门由实测确定**：`verify.probe_selectors()` 在改前代码上跑
全部候选测试，失败的成为 `target_tests`，通过的成为 `regression_tests`。
这直接对应题面「修改前因目标行为未实现而失败」的定义。改完后测试不再
失败，因此选择结果存入状态复用，而不是重新猜测。

## 各项技术的分工

- **MiniMax M3.1 Flash**：唯一负责生成代码差异。Prompt 里给出任务、选中
  来源全文（带路径/行号/版本）、在范围内的文件、验收测试、必须保持通过的
  测试。响应经 `repair_hunk_counts()` 归一化（重算 hunk 计数、删除不存在
  的 blob hash），再由 `apply_patch()` 应用并以**内容 hash 变化**确认生效。
- **seekdb**：文档表 `docs` 存 spec/code/test 全文与元数据，`doc_terms`
  存倒排词项。上下文条目的每一个来源都来自实际 SQL 查询；`seekdb_queries.jsonl`
  逐条记录执行的 SQL、时间与命中行数。
- **PowerContext 1.2.0**：`minimax-task-state` Memory entry 存完整状态
  （任务、检索词、选中来源、目标文件、验收/回归测试、补丁信息、验证结果、
  事件历史）。新进程通过 `runtime.memory.for_scope(...).list()` 读回，
  `cli status` 即此路径。

## 已知限制

- 业务工程是刻意做小的 demo（6 本书的 FastAPI 服务），不是生产系统。
- 检索是词法级 + 结构化先验，不引入 embedding；对同义改写（不含共同词且
  规格也没提到）会退化。
- 规格只有 OpenSpec Markdown 一种格式被识别为 spec 角色；其他格式会当作
  普通文档参与检索。
- PowerContext 固定 1.2.0：1.4.1 的 Runtime 接口已变（`runtime.memory`
  形态不同），本模块不做多版本适配。
- `apply_patch` 需要 `git` 或 `patch(1)` 之一；两者都会校验文件确实变化，
  避免「报告成功但没改」的静默失败。
