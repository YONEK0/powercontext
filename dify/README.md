# 忘事管理局 · 失物调查与出门检查

“东西放哪了”只是起点。值班员阿档会根据上次位置和新线索陪你排查，记住哪些地方已经找过，换新对话继续同一案件；找到后询问收纳安排。出门前，它会把物品位置、借出和电量记录变成本次检查清单，分别确认取回、充电和装包。

本版采用 **Dify 0.15.3 + DeepSeek deepseek-flash + PowerContext 1.4.1**。DeepSeek 负责自然语言理解和排查建议；Dify 承担两次模型调用、校验、分支和 HTTP 编排；后端校验业务状态，只有真实的 PowerContext 写入回执才能触发“档案已同步”。需要使用者自己的 DeepSeek API Key，有实际模型调用费用。

## 快速启动

验证平台为 Windows + WSL Ubuntu 24.04 / Linux amd64，Docker Engine 29.9.3、Compose 5.7.1。需要 Python 3、Bash、已安装的 Docker Engine 和 Compose 插件，建议至少 4 GB 可用内存、15 GB 磁盘。首次启动需要访问 Docker Hub、PyPI 和 DeepSeek API。

在 Linux 或 WSL 的 Linux 文件系统里获取本仓库的赛题分支：

```bash
git clone --branch 2026-competition-ecosystem https://github.com/YONEK0/powercontext.git
cd powercontext/dify

# 在当前终端安全输入自己的 DeepSeek API Key，输入内容不会回显。
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
bash scripts/start.sh
unset DEEPSEEK_API_KEY
```

脚本会生成 `.env` 中的本地密码和内部服务凭据，启动完整 Dify 栈，初始化管理员，将 DeepSeek Key 校验后加密保存到 Dify，导入并发布工作流，打印实际聊天地址。首次启动模型凭据校验也会发出小额真实请求。再次启动已有实例不必重复输入 Key。

默认控制台为 `http://127.0.0.1:18081/apps`，实际聊天地址是输出的 `/chat/<code>`。管理员信息位于 `.runtime/admin.json`；验收 API 配置位于 `.runtime/connection.json`。它们只在本机生成，不在交付包里。聊天页可直接使用，控制台需要管理员登录。

如果 WSL 中 Docker 已安装但没有启动，可运行 `sudo python3 scripts/start_docker_wsl.py`。本包不会自动安装宿主机 Docker 或修改系统代理。Docker 安装以官方 Ubuntu 安装说明为准。

## 三分钟体验

在同一个浏览器的聊天页依次输入：

```text
相机在蓝色背包，电池还没充，三脚架借给小张了。
我的相机找不到了
蓝色背包找过了，没有相机。
昨天换成黑色相机包去拍照了，可能忘在里面。
```

点击“新对话”后输入“继续办案”：应该继续相机案件，保留排查记录，依据新线索追问。

```text
相机找到了，在黑色相机包里。
现在把相机放在书桌右侧抽屉，以后也固定放这里。
明天出去拍照，只核对相机、电池、三脚架，做出门检查。
电池充好了，但还没装包。
相机已装包，电池也装包了。
这次不带三脚架。
```

电池充电和装包分别打勾；借出的三脚架需要取回或明确本次不带。说“相机从包里拿出来了，还没重新装包”会撤回对应完成状态。再换新对话询问“查看我这次的出门清单，还差什么”，仍能看到进度。后天重新建立清单时，所有事项重新确认。

## 模型和 Key

模型固定为本次真实联调可用的 **`deepseek-flash`**。Dify 控制台中使用“OpenAI-API-compatible”自定义提供商，模型名 `deepseek-flash`、Chat 模式、Base URL `http://memory-bridge:19080/model/v1`、上下文 32768、最大生成 1800 tokens、禁用 Function Calling 与 Vision。完整配置和手动导入步骤见 [docs/模型配置.md](docs/模型配置.md)。

Dify 0.15.3 会过滤 DeepSeek 的专用参数，因此 `backend/model_proxy.py` 仅做协议适配：固定转发到 DeepSeek 官方地址，并设置 `thinking=disabled` 和 JSON 输出。它不代替 Dify 执行业务或模型规划。真实 Key 来自 Dify 保存的提供商凭据，转发时只在内存中使用，不写入后端源码、DSL 或审计日志。

每轮通常有两次真实模型请求；模型 A 失败会停止本轮，模型 B 失败时采用后端的保守追问/基础清单。模型审计在桥接数据卷 `/data/model-usage.jsonl`，仅保存 request ID、token 数、模型名和耗时。Dify 自定义提供商的默认价格可能显示为 0，这不代表 DeepSeek 免费；实际费用以 DeepSeek 账单为准。

## 验收

```bash
# 只读就绪检查
python3 scripts/diagnose.py

# 15 项业务单元测试：使用假的存储，只验证校验、隔离、回滚与状态规则
docker exec lostfound-dify-memory-bridge-1 python test_bureau.py

# 真实 Dify + DeepSeek + PowerContext 验收，有模型费用
python3 scripts/verify_ai_e2e.py

# 额外测试模型禁用、PowerContext 断开、容器重建；短暂影响本实例
python3 scripts/verify_ai_e2e.py --with-outages
```

真实验收读取已发布应用的消息和数据库中实际节点输出，检查意图、案件、清单、用户隔离、真实 artifact ID 和故障回复，而非只匹配模型的一句回答。报告默认写入 `evidence/ai-e2e.json`，任何断言失败返回非零。每次采用不同测试用户，不污染浏览器访客的档案。执行机器需要 Docker 权限。

验证样例见 [docs/验证样例.md](docs/验证样例.md)，架构和约束见 [docs/工作流与状态.md](docs/工作流与状态.md)，本机联调结果见 [docs/改造与验收报告.md](docs/改造与验收报告.md)。`evidence/` 是人工测试数据的证据，不会自动导入新实例。

## 部署与维护

必须通过 `scripts/compose.sh` 管理服务，它负责官方 Compose 的路径和覆盖配置：

```bash
bash scripts/compose.sh ps
bash scripts/compose.sh logs --tail=80 api memory-bridge powercontext
bash scripts/compose.sh down          # 保留数据卷
bash scripts/start.sh                 # 复用管理员、模型、应用和档案
```

PowerContext 是状态的持久化来源；桥接卷仅保存用户 scope 映射和不含 Key 的模型调用审计。Dify、PowerContext 和记忆桥的命名卷均需保留。不要通过删除数据卷完成日常重启。

默认项目名 `lostfound-dify`，含 11 个服务。另开独立实例需使用不同目录、项目名与端口，并向脚本提供相应地址：

```bash
LOSTFOUND_PROJECT=lostfound-second \
EXPOSE_NGINX_PORT=127.0.0.1:18082 \
EXPOSE_NGINX_SSL_PORT=127.0.0.1:18444 \
BRIDGE_PORT=19081 \
DIFY_BASE_URL=http://127.0.0.1:18082 \
BRIDGE_BASE_URL=http://127.0.0.1:19081 \
bash scripts/start.sh
```

已有 Dify 实例可以提供 `DIFY_ADMIN_EMAIL` 和 `DIFY_ADMIN_PASSWORD` 来运行导入脚本；不会重置已有密码。`.runtime/connection.json` 存在时会更新其中指定的应用，导入另一个应用应使用独立目录和新的 connection 文件。手动导出 DSL 时请恢复内部 Token 占位符，不要把本机运行配置直接提交。

## 边界

- 按 Dify `sys.user_id` 为每个用户分配独立 PowerContext scope。同一浏览器的“新对话”保留身份；换浏览器、清除浏览器数据或换 API user 会成为另一用户。尚无跨设备账号合并。
- 无主动通知、日历推送、位置感知或真实物品定位。排查建议明确标记“未确认”；“明天”等日期按服务器 Asia/Shanghai 解析。
- 首版为单进程桥接，使用版本冲突检查；不是支持多副本同时写入的服务。输入最多 2000 字，每轮最多 8 个动作，事件小于 7900 UTF-8 字节，上下文最多 24 KB，超出会明确失败。
- 当前检索为全文检索和明确名称匹配，未引入 Embedding / Rerank，不承诺所有同义名称自动合并。大规模历史的压缩、归档与 UI 管理尚未实现。
- 仅验证 Dify 0.15.3 和 Linux amd64。保留旧版后端 `/remember`、`/recall` 供原演示兼容；它们属于旧共享档案，新版应用只走带内部凭据的 `/v2/` 用户隔离接口。

赛题路线为 Dify + PowerContext，无需额外部署 seekdb。作品位于 `2026-competition-ecosystem` 分支的 `dify/` 目录，包含源码、依赖、工作流和复现材料。比赛网站提交流程以赛事要求为准。
