# 记仇小妖 GrudgeImp

> Dify × OceanBase 大赛 · Dify 赛道（`dify/`）
> 一个会记仇的 NPC：你说过的每件坏事它都记着，下次见面翻旧账。

## 1. 作品是什么

「记仇小妖」是一只住在旧键盘缝里的小妖怪。你每对它做一件坏事（把咖啡换成酱油、藏起充电线、说它代码有 bug），它都会记在小本本上；下次你再来，它会把旧账翻出来，阴阳怪气地还给你。

**玩法**：打开 Dify 应用，直接聊天。
- 说一件新的坏事 → 小妖吐槽，并**记住**
- 换一个新会话问「你还记得我干过什么吗」→ 小妖**翻出旧账**（跨会话记忆）
- 说无关的话 → 小妖正常接话，**不硬编**旧事

好玩点在于：它的「记仇」是真的存在数据库里的，不是靠长上下文硬撑——新开会话、重启服务，它都记得。

## 2. 技术分工

| 技术 | 在本作品里的作用 | 落点 |
|---|---|---|
| **Dify**（必选） | Chatflow 编排全部流程：接收输入 → HTTP 召回记忆 → LLM 生成回复 → HTTP 写入记忆 → 输出 | `GrudgeImp.yml` |
| **PowerContext**（二选一） | 记忆的持久化存储与语义召回：每次对话写入一条原子记忆，下次按 query 检索出来注入提示词 | `backend/memory_bridge.py` 调用 PowerContext HTTP API |
| seekdb | 本作品**未使用**（赛题要求 seekdb / PowerContext 至少一种） | — |

为什么加一个 bridge：Dify 的 HTTP Request 节点不处理鉴权、Scope 创建和 JSON 整形，桥接层把 PowerContext 的三个动作（建 Scope / 写记忆 / 搜记忆）包成 `/remember`、`/recall` 两个傻瓜接口，也让 DSL 保持 5 个节点、易读易改。

```
用户 ──▶ Dify Chatflow ──▶ HTTP POST /recall ──▶ bridge ──▶ PowerContext /v1/memory/search
                │                                                   │
                ▼                                                   │
             LLM 生成回复（提示词里带旧账）                          │
                │                                                   │
                ▼                                                   ▼
           HTTP POST /remember ──▶ bridge ──▶ PowerContext /v1/memory/remember
                │
                ▼
              Answer
```

## 3. 版本与环境

| 组件 | 版本 | 说明 |
|---|---|---|
| Dify | 1.x（DSL `version: 0.1.3`，`mode: advanced-chat`） | 社区版即可，需在「设置 → 模型供应商」里配好一个对话模型 |
| PowerContext | 1.4.1（Server + HTTP API） | `pip install "powercontext[server,client]==1.4.1"`，默认监听 `127.0.0.1:18080` |
| Python | ≥ 3.11 | 桥接服务 |
| 桥接依赖 | `fastapi`、`uvicorn` | 见 `backend/requirements.txt` |
| 模型 | 任意 OpenAI 兼容对话模型 | DSL 里默认 `langgenius/openai/openai` + `gpt-4o-mini`，导入后可一键切换 |

> 记忆召回走 PowerContext 的文本通道，**不需要** embedding / rerank 服务；想启用向量通道可在 PowerContext 侧配置，本作品不依赖。

## 4. 运行步骤

### 4.1 启动 PowerContext（记忆服务）

```bash
pip install "powercontext[server,client]==1.4.1"
powercontext server run --host 127.0.0.1 --port 18080
# 另开终端验证
curl -s http://127.0.0.1:18080/health/live      # {"status":"ok"}
```

### 4.2 启动记忆桥

```bash
cd backend
pip install -r requirements.txt
PC_BASE_URL=http://127.0.0.1:18080 python memory_bridge.py     # 监听 0.0.0.0:19080
```

首次启动会自动创建（或复用）一个名为「记仇小妖 · GrudgeImp」的 Scope，并把 `scope_id` 缓存到 `.scope.json`。

```bash
curl -s http://127.0.0.1:19080/health
# {"ok":true,"pc_base_url":"http://127.0.0.1:18080","scope_id":"scp_...","memories":0}
```

### 4.3 导入 DSL

1. Dify → 工作室 → 创建空白应用 → **导入 DSL 文件** → 选择 `GrudgeImp.yml`；
2. 打开应用 → 右上角「模型」→ 选择你已配置的对话模型（默认 `gpt-4o-mini`，可换成任意可用模型）；
3. 检查两个 HTTP 节点的地址（Dify 在 Docker 里跑时用 `host.docker.internal:19080`；Dify 在宿主机直接跑时改成 `127.0.0.1:19080`）：
   - `召回旧账` → `http://host.docker.internal:19080/recall`
   - `记一笔` → `http://host.docker.internal:19080/remember`
4. 发布 → 进入应用对话页，开始玩。

### 4.4 验证

按 [`docs/验证样例.md`](docs/验证样例.md) 走一遍：说一件坏事 → 新开会话问「还记得吗」→ 小妖翻旧账；再看 PowerContext 里条目数变化。

## 5. 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `PC_BASE_URL` | `http://127.0.0.1:18080` | PowerContext 地址 |
| `PC_TOKEN` | 空 | PowerContext 开启鉴权时填写 |
| `PC_SCOPE_ID` | 空 | 指定已有 Scope；不填则自动查找/创建 |
| `PC_SCOPE_TITLE` | `记仇小妖 · GrudgeImp` | 自动创建时的 Scope 名称 |
| `PORT` | `19080` | 桥接服务端口 |

## 6. 目录

```
dify/
├── GrudgeImp.yml            # Dify Chatflow DSL（5 节点）
├── README.md                # 本文件（运行说明）
├── docs/验证样例.md          # 可执行的验证样例
├── backend/
│   ├── memory_bridge.py     # PowerContext 记忆桥
│   ├── requirements.txt
│   └── .env.example
└── evidence/                # 实测记录（桥接、PowerContext 原始返回、DSL 结构校验）
```

## 7. 已知限制

1. **DSL 未在本机导入验证**：开发环境没有 Docker / Dify 实例，`GrudgeImp.yml` 是按 Dify `0.1.3` advanced-chat 结构编写的（节点、边、变量引用都做了静态校验，见 `evidence/04-dsl-structure.json`）。若你的 Dify 版本对字段有差异，导入时按提示调整两个 HTTP 节点的 URL 即可。
2. **模型需自备**：DSL 里写的是 `langgenius/openai/openai` + `gpt-4o-mini`，导入后请换成你环境里可用的模型。
3. **记忆召回是文本通道**：没有配置向量模型时，PowerContext 用全文匹配召回；语义相近但用词不同的旧账可能召不回，可以给 PowerContext 配 embedding 提升。
4. 桥接服务只监听内网，没有鉴权；生产使用请加 token（`PC_TOKEN` 已支持）。
