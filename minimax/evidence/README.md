# 验证证据

最终一次完整运行在 `final/`，内容与来源见 `final/README.md`。
其余子目录是运行时生成物，默认不提交（见 `.gitignore`）。

- `before/`、`after/`：业务代码快照，随仓库提交，用于 `cli restore`。
- `final/`：最终一次完整运行的证据（提交）。
- `tests/`：测试日志，每次运行生成，含命令、cwd、退出码、代码版本。
- `contexts/`：每次 `cli context` 的输出快照。
- `state/`：每次状态写入的镜像。
- `minimax/`：MiniMax 请求/响应记录与本次生成的 patch。
- `seekdb_queries.jsonl`：累计的 SQL 日志。

不得提交真实 API Key。`MINIMAX_API_KEY` 只放本地 `.env` 或环境变量。
