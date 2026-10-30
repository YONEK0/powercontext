# 2026 年 OceanBase 数据库大赛 · Qoder 赛道提交材料

## 必填信息

| 提交项 | 内容 |
|---|---|
| 项目名称 | 知溯 Zhishu——本地学习知识库与主动复习助手 |
| 项目网站网址 | https://zhishu-study-712i5wlfkz3.qoder.zone |
| 代码仓库 | https://github.com/YONEK0/powercontext/tree/2026-competition-ecosystem/qoder |
| Qoder 账号 UID | 待参赛者填写 |
| 指定技术 | OceanBase seekdb embedded |
| 开发工具 | Qoder Desktop |

## 上传材料

- [ ] 完整项目源码：`zhishu-source.zip`（也用于导入赛题指定仓库的 qoder 目录）
- [ ] 运行说明：`README.md`
- [ ] 验证样例：`VERIFICATION.md`
- [ ] Qoder 开发记录：`QODER_DEVELOPMENT.md`，附本次实际对话的关键截图或录屏
- [ ] 公开访问验证截图：`live-desktop.png`、`live-mobile.png`
- [ ] 本提交信息表：`SUBMISSION.md`

## 项目简介（可用于平台填写）

知溯解决学习笔记散落、学过却找不到原文、复习只重读的问题。用户可以导入 Markdown 或文本笔记，通过 seekdb 的 HNSW 向量索引、全文索引与 RRF 混合检索找回原文出处，再将核心知识生成复习卡，根据「模糊、熟悉、掌握」反馈更新掌握度和下次复习时间。每次检索展示命中 ID、索引与审计事件，指定技术的实际作用可核对。项目由 Qoder 开发，完全本地运行，无需 API Key。

## 发布与访问验证

- Qoder Sites 发布状态：已发布（2026-10-30，发布操作成功并已提交）
- 可见范围：public，任何人都可访问
- 未登录访问验证：全新无痕浏览器会话打开首页返回 200，未跳转登录；标题「知溯 Zhishu · 让知识找得到，也记得住」；真实运行截图与 4 个下载文件（源码 ZIP、README、验证样例、Qoder 开发记录）均返回 200；无 JavaScript 报错
- 移动端验证：390px 视口无横向溢出，截图正常
- 线上网站用途：项目展示、真实运行截图、源码与复现材料下载
- 核心应用入口：按运行说明在本地启动，确保 seekdb 数据与检索功能可完整复现

## 使用效果材料

当前不提交虚构的使用人数、使用次数或提效数据。已有验证仅包括自动化测试和开发者本地功能验证，不将其包装成真实用户效果。

## 提交前最终检查

- [x] 网站在未登录状态下可打开，截图与下载链接正常。
- [x] 源码仓库公开或已向评审开放，分支 / commit hash 基于赛题指定分支（`2026-competition-ecosystem` / `qoder/`）。
- [ ] 按 README 在干净环境跑通，验证样例结果一致。
- [ ] Qoder UID 与网站归属一致，开发截图或录屏清晰可见。
- [ ] 配置示例无真实 API Key、密码或其他凭据。
- [ ] 评审结束前不暂停或删除站点；修改网站后重新发布。
