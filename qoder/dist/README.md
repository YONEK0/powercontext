# 知溯 Zhishu

一个由 **seekdb** 驱动的本地学习知识库：把分散笔记收进来，用向量与全文混合检索找回原文出处，再将核心知识转成复习卡，根据反馈安排下次复习。

## 解决的真实问题

- 笔记散落在不同文件里，记得学过却找不到原文。
- 搜索只返回相似内容，无法核对来源与检索依据。
- 复习以重复阅读为主，不知道哪些内容真正掌握。

知溯形成「导入 → 检索与溯源 → 主动回忆 → 间隔复习」闭环。项目完全本地运行，不需要 API Key，不下载模型，不上传笔记。

## 核心功能

- Markdown / 纯文本导入、自动分段、分类与来源管理。
- seekdb HNSW 向量检索、全文检索、RRF 混合检索和结构化分类过滤。
- 检索结果展示原文、出处、排序分、命中 ID 与本次审计事件。
- 从笔记核心句生成复习卡；支持「模糊 / 熟悉 / 掌握」反馈。
- 自动更新掌握度、复习间隔和到期时间，重复生成不会覆盖学习进度。
- 操作记录、使用统计和 Markdown 导出。

## 技术架构

```text
浏览器（原生 HTML / CSS / JavaScript）
               │ JSON
               ▼
        Flask 应用与输入校验
               │
               ▼
       pyseekdb / seekdb embedded
  ┌────────────┼─────────────┐
  │ HNSW 向量索引 │ ngram 全文索引 │
  │ 128 维 cosine │ 二元 token     │
  └────────────┼─────────────┘
               ▼
        RRF 融合 + 结构化过滤
               ▼
 笔记片段 / 复习卡 / 检索与复习事件
```

所有应用数据都在 seekdb 的 `zhishu_v1` collection 中，通过 `kind = note / card / event` 区分。向量由本地确定性字符 n-gram 算法生成，因此无需外部模型；这里不将其宣称为大模型语义嵌入。

## 本地运行

### 环境

- Python 3.11–3.13（已在 Python 3.13.5、macOS arm64 验证）
- pip
- 无需 Docker、Node.js 或外部数据库服务

### 安装与启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python seed_demo.py
.venv/bin/python app.py
```

浏览器打开 **http://127.0.0.1:5000**。示例只需初始化一次；再次执行 `seed_demo.py` 不会覆盖已有数据。

如需使用空知识库，可跳过 `seed_demo.py`，在页面点击「导入笔记」。自定义监听地址或数据目录：

```bash
HOST=127.0.0.1 PORT=5000 ZHISHU_DATA=/path/to/seekdb .venv/bin/python app.py
```

### 运行测试

```bash
.venv/bin/python -m unittest -v test_app.py
```

测试在临时目录创建独立 seekdb 数据库，不修改正式笔记。

## 使用流程

1. **载入示例**：首次启动执行 seed 脚本，获得 4 篇笔记和 12 张复习卡。
2. **混合检索**：进入「混合检索」，查询 `怎样对抗遗忘`，查看《遗忘曲线与间隔重复》及检索轨迹。
3. **核对出处**：点击结果下方「核对原文」，检查完整笔记。
4. **导入资料**：点击「导入笔记」，选择 `.md` / `.txt` 文件或粘贴正文。
5. **生成复习卡**：在知识库打开笔记，点击「生成复习卡」。
6. **主动复习**：进入「复习空间」，先回忆再显示答案，选择掌握反馈。
7. **验证持久化**：刷新页面或重启服务，笔记、掌握度和复习日期仍然保留。

详细验收步骤见 [VERIFICATION.md](VERIFICATION.md)。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | seekdb 健康状态 |
| GET | `/api/overview` | 学习统计与最近事件 |
| GET / POST | `/api/notes` | 笔记列表 / 导入 |
| GET / DELETE | `/api/notes/<note_id>` | 原文 / 删除笔记及关联卡片 |
| POST | `/api/notes/<note_id>/cards` | 生成复习卡 |
| POST | `/api/search` | 向量、全文或混合检索 |
| GET | `/api/review` | 复习队列 |
| POST | `/api/review/<card_id>` | 保存复习反馈 |
| POST | `/api/demo` | 空库时载入示例 |
| GET | `/api/export` | 导出 Markdown |

## 文件说明

| 文件 | 作用 |
|---|---|
| `app.py` | Flask 路由、输入校验与应用工厂 |
| `store.py` | seekdb 索引配置、检索、复习与审计记录 |
| `text_utils.py` | 文本清理、切分、本地向量与复习卡提取 |
| `index.html` / `styles.css` / `app.js` | 响应式项目网站与完整交互 |
| `sample_notes.json` / `seed_demo.py` | 可复现示例数据与初始化 |
| `test_app.py` | 单元及端到端 API 测试 |
| `VERIFICATION.md` | 评审验证样例 |
| `QODER_DEVELOPMENT.md` | Qoder 开发过程记录 |

## 数据与隐私

- 默认数据保存在 `data/seekdb/`，该目录不纳入版本控制。
- 配置示例不含真实凭据；应用没有外部 API 凭据需求。
- 删除笔记会同时删除其关联复习卡，页面操作前会要求确认。
- 示例内容为本项目编写的学习笔记，不依赖第三方数据集。

## 交付说明

项目网站用于作品展示和材料下载，核心检索与复习功能在本地 seekdb 应用中运行。公开网址、代码仓库地址和 Qoder 账号 UID 记录于测试平台提交材料；不要使用占位网址冒充可访问站点。
