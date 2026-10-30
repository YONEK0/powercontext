# Qoder 开发记录

## 项目与赛题

- 项目：知溯 Zhishu——本地学习知识库与主动复习助手
- 赛道：Build with Qoder
- 指定技术：OceanBase seekdb（实际使用，不是仅安装或测试连接）
- 开发日期：2026-10-30
- Qoder 账号 UID：**提交前由参赛者补充**

## Qoder 在关键环节完成的工作

### 1. 赛题理解与方案拆解

Qoder 阅读 OceanBase 大赛 Qoder 赛题页面，提取基础验收条件、技术结合要求与提交材料，并将任务拆成方案与环境检查、核心开发、功能验证、交付文档四个阶段。

确定从「笔记找不到、复习只重读」的真实学习痛点出发，形成导入、混合检索、原文溯源、复习卡和间隔复习闭环。为避免评审环境依赖外部服务，选择 seekdb embedded 和本地确定性向量，不要求 API Key。

### 2. 技术验证与编码

Qoder 检查本机 Python 环境，创建虚拟环境并安装 `pyseekdb` 与 Flask；阅读已安装 SDK 的接口定义，实际验证：

- `Schema`、`VectorIndexConfig`、`HNSWConfiguration` 的 128 维 cosine 向量索引。
- `FulltextIndexConfig` 的二元 ngram 全文索引。
- `collection.query` 向量查询与 `collection.hybrid_search` 全文 / RRF 混合查询。
- 手动向量写入、元数据过滤、索引刷新和复习状态更新。

随后由 Qoder 编写 Flask 后端、seekdb 数据访问层、文本处理算法、示例数据、响应式网站和前端交互。主要开发产物包括 `app.py`、`store.py`、`text_utils.py`、`index.html`、`styles.css`、`app.js` 等。

### 3. 调试与验证

Qoder 先通过小数据库实验修正集合配置和全文查询调用方式，再编写并运行 `test_app.py`。实际执行结果：3 项测试全部通过。

Qoder 启动本地服务并在浏览器走通：示例知识库、混合检索、命中 ID / 审计事件展示、笔记导入、复习卡生成和复习反馈。浏览器实测中，复习后待复习数由 12 变为 11；新导入「康奈尔笔记法」后生成 3 张卡片。

### 4. 交付整理

Qoder 编写运行说明、API 表、验证样例和本开发记录，明确区分已完成本地交付与后续待办（公开仓库、Qoder Sites 发布、账号 UID），未生成虚假网址或效果数据。

## 可核对的开发证据

- 完整源码和依赖锁定文件在当前项目目录。
- `test_app.py` 可重复运行，使用临时 seekdb 数据库。
- `VERIFICATION.md` 记录实际查询、预期结果和本次浏览器验证。
- 应用「技术与验证」页及每次检索的轨迹面板展示真实引擎、索引、命中 ID 与事件 ID。
- seekdb 中的 `event` 记录可核对导入、检索、复习卡生成与复习操作。

## 发布与提交

Qoder Sites 的发布结果与公开访问验证记录在测试平台提交材料中。提交前仍需：

- 将源码放入赛题要求的仓库与 qoder 目录，并记录分支 / commit hash。
- 补充本文件中的 Qoder 账号 UID，并选取本次实际对话的关键截图或录屏。
- 如提交使用效果，只填写真实用户与可核对来源；当前没有虚构用户量或提效数据。
