# 忘事管理局 · Lost & Found Bureau

> Dify 赛道（Just for Fun）｜`dify/`｜分支 `2026-competition-ecosystem`

## 0. 一键跑起来（推荐，全容器）

```bash
cd <dify源码>/docker
docker compose -f docker-compose.yaml \
  -f <本仓库>/dify/deploy/docker-compose.full.yaml up -d --build
```

会同时起：Dify 0.15.3 全栈 + **PowerContext**（记忆存储）+ **记忆桥**（`memory-bridge`，把 PowerContext 包装成两个 HTTP 接口）。

1. Dify → 工作室 → 导入 `dify/LostFound.yml`；
2. 应用设置里选一个对话模型（默认 `openai / gpt-4o-mini`）；
3. 发布 → 创建 API Key；
4. 验证：

```bash
DIFY_BASE=http://127.0.0.1 DIFY_API_KEY=app-xxxx bash deploy/verify_e2e.sh
# 末行应为：✅ 主路径通过
```

> DSL 里两个 HTTP 节点用环境变量 `MEMORY_BRIDGE_URL`（默认 `http://memory-bridge:19080`）。
> 想把记忆桥跑在宿主机，把这个值改成 `http://host.docker.internal:19080`，并用
> `deploy/docker-compose.override.yaml` 给 api / worker / **ssrf_proxy** 加 host 映射。

## 1. 作品是什么

一个**帮人记事**的小工具，但被做成了官僚机构的语气：

- **存**：`存：备用钥匙在玄关第二个抽屉` → 归档，返回回执；
- **查**：`查：钥匙在哪` → 从档案里检索，管理员用欠揍的语气告诉你；
- 其他输入 → 使用说明。

好玩点在于：它真的记得（PowerContext 持久化），换会话、重启都还在；而查询走的不是"把整段对话塞给模型"，是**先检索再回答**。

## 2. 技术分工

| 技术 | 作用 | 落点 |
|---|---|---|
| **Dify**（必选） | Chatflow 编排：关键词分流（if-else）→ 检索/归档 → LLM → 回复 | `LostFound.yml`（8 节点） |
| **PowerContext**（二选一） | 记忆的持久化与检索：每条登记写一条 memory，查询时按关键词召回 | `backend/memory_bridge.py` 调 PowerContext HTTP API |

流程：

```
用户输入
  └─ if-else
      ├─ 含「查/找」 → HTTP /recall → LLM（带档案）→ 回答
      ├─ 含「存/记」 → HTTP /remember → 回执
      └─ 其他      → 使用说明
```

## 3. 版本与依赖

| 组件 | 版本 |
|---|---|
| Dify | 0.15.3（DSL `0.1.3`，`advanced-chat`；1.x 导入会升级到 0.1.5） |
| PowerContext | 1.4.1（容器内运行，端口 18080） |
| 记忆桥 | FastAPI + uvicorn（`backend/`，端口 19080） |
| 模型 | 任意 OpenAI 兼容对话模型（默认 `gpt-4o-mini`，导入后可在应用里更换） |

## 4. 验证

见 [`docs/验证样例.md`](docs/验证样例.md)：三条用例（存 / 查 / 兜底）+ PowerContext 记忆条数核对。

实测记录：[`evidence/10-lostfound.log`](evidence/10-lostfound.log)（Dify 0.15.3 全容器，导入 → 发布 → 三条用例 200，记忆 2→3）。

## 5. 目录

```
dify/
├── LostFound.yml           # Dify Chatflow DSL
├── README.md
├── docs/验证样例.md
├── backend/                # 记忆桥（memory_bridge.py + Dockerfile + requirements）
├── deploy/                 # 全容器 compose、PowerContext 镜像、诊断/验证脚本
└── evidence/               # 实测日志
```

## 6. 已知限制

1. 查询分支需要模型；没配模型时「存」和「说明」仍可用，但「查」会失败。
2. 检索是关键词/全文匹配（PowerContext 文本通道），近义改写可能召不回；配 embedding 可提升。
3. 记忆桥无鉴权，仅监听容器网络；生产需加 token（`PC_TOKEN` 已支持）。
