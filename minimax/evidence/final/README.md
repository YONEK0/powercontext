# 最终验证证据

本目录是一次完整运行的真实产物，由 `cli` 在空状态下依次执行
`init → context → test(target) → agent --replay --apply → test(target+regression)
→ status` 生成。没有手工编辑。

| 文件 | 内容 |
|---|---|
| `target-before.log` | 改前跑验收测试：`exit_code: 1`，并记录命令、cwd、代码版本 hash |
| `target-after.log` | 改后跑验收测试：`exit_code: 0` |
| `regression-after.log` | 改后跑 3 个回归测试：`exit_code: 0` |
| `patch.diff` | 实际应用的 unified diff（已归一化 hunk 计数） |
| `context.json` | 上下文输出：检索词、三组来源（路径/行号/版本/hash/分数/命中词）、目标文件、验收与回归测试、探针结果 |
| `seekdb_queries.jsonl` | 实际执行的 SQL、时间戳、命中行数 |
| `state-status.json` | **新进程**从 PowerContext 读回的任务状态 |

## 与六项必做的对应

1. **能够运行** — `README.md` 的 6 步可从干净环境执行；数据文件
   `demo_app/data/books.json` 随仓库提交，不依赖本地状态。
2. **MiniMax 实际参与** — `context.json` 中的来源被完整送入 MiniMax prompt；
   生成的 diff 见 `patch.diff`。原始请求/响应记录在
   `evidence/minimax/*.json`（本地运行生成，未提交），
   历史一次记录的副本保留在仓库说明中。
3. **上下文正确** — `context.json` 的 `required` 列出全部必需来源；
   `tests/test_retrieval.py` 覆盖「规格更新后重新导入，输出使用新内容」。
4. **数据库实际参与** — `seekdb_queries.jsonl` 是 `pylibseekdb` 实际执行的
   SQL；`context.json` 里每个条目都来自这些查询。
5. **状态能够复用** — `state-status.json` 由**另一个** CLI 进程生成，
   读出 `status: verified`、目标/回归测试选择、两次测试退出码与代码版本。
6. **代码修改有效** — `target-before.log` exit 1 → `target-after.log` exit 0，
   `regression-after.log` exit 0；三者 `code_version` 不同，证明对应不同代码版本。

## 关于 `context.json` 中的代码版本

`context.json` 里 `code:demo_app/app.py` 的 `version` 是 **改前** 版本。
这是正确的：上下文是修改的**输入**，必须在补丁应用前生成。改动后的版本
见 `target-after.log` 的 `code_version`。

## 关于 MiniMax 记录

- 无 Key 环境用 `agent --replay` 复现已验证的补丁（`patches/recorded.patch`）。
- 有 Key 环境用 `agent --live` 重新调用，`evidence/minimax/` 会记录本次
  真实请求与响应。评审不要求模型重新生成完全相同的补丁。
- 模型原始响应可能带错误 hunk 计数或不存在的 `index <sha>..00000` blob；
  `agent.py` 在应用前归一化，`patch.diff` 是归一化后的结果。

## 复现方式

见 `../VALIDATION.md` 与 `../README.md`。重置用 `bash scripts/reset.sh`。

日志里的 `cwd:` 与解释器路径是生成这次证据时的实际路径，仅供核对来源；
按 `README.md` 在你自己的环境重跑会得到同样的退出码与 `code_version`，
路径自然不同。
