# 验证样例

## 任务

**为 `GET /books` 增加包含边界的价格区间过滤，且不改变现有 `q` / `category` 行为。**

- 业务工程：`demo_app/`，一个 FastAPI 图书检索服务，数据为 `demo_app/data/books.json`（6 本）。
- 初始状态：`list_books` 的函数签名已有 `min_price` / `max_price`，但**没有应用**。
- 有效规格：
  - `openspec/specs/book-search/spec.md`（生效规格）
  - `openspec/changes/add-price-filter/specs/book-search/spec.md`（变更 delta，规范性定义）
  - `openspec/changes/add-price-filter/{proposal,design,tasks}.md`（变更说明）
- 任务文本不写死：`cli` 从 `openspec/changes/add-price-filter/proposal.md` 的
  `## What Changes` 段落自动生成；也可 `--task` 覆盖。

## 上下文中必须包含的来源

评审可直接对照 `evidence/final/context.json` 的 `required` 字段。

| 类型 | 必须包含 | 说明 |
|---|---|---|
| spec | `spec:openspec/changes/add-price-filter/specs/book-search/spec.md` | 变更的规范性定义 |
| spec | `spec:openspec/specs/book-search/spec.md` | 生效的完整规格 |
| code | `code:demo_app/app.py#list_books` | 需要修改的符号 |
| code | `code:demo_app/app.py` | 所在文件（含数据加载） |
| test | `test:demo_app/tests/test_books.py#test_price_filter_is_inclusive_and_keeps_order` | 验收测试 |
| test | `test:demo_app/tests/test_books.py#test_query_filter_still_works` | 回归 |
| test | `test:demo_app/tests/test_books.py#test_category_filter_still_works` | 回归 |
| test | `test:demo_app/tests/test_books.py#test_unfiltered_list_is_unchanged` | 回归 |

## 要改什么 / 哪些行为必须保持

要改：在 `q`、`category` 过滤之后，按 `price >= min_price` 和
`price <= max_price` 过滤，边界包含，不排序。

必须保持：

| 测试 | 保护的行为 |
|---|---|
| `test_query_filter_still_works` | `/books?q=ada` 仍返回两本 Ada 的书 |
| `test_category_filter_still_works` | `/books?category=fiction` 仍返回两本小说 |
| `test_unfiltered_list_is_unchanged` | `/books` 仍返回 6 本、顺序不变 |

## 初始数据

`demo_app/data/books.json`，6 本，价格 18/12/9/25/8/30。10–20 区间内
按原始顺序为 `[18.0, 12.0]`，这是验收测试断言的值。

## 操作步骤与预期结果

```bash
export PYTHONPATH=src

python -m minimax_context.cli init                    # spec 5 / code 4 / test 5
python -m minimax_context.cli context                 # 探针：1 失败 3 通过
python -m minimax_context.cli restore before
python -m minimax_context.cli test --target           # 预期 exit 1
python -m minimax_context.cli agent --replay --apply   # 预期 applied_by=git
python -m minimax_context.cli test --target --regression   # 预期两项均 exit 0
python -m minimax_context.cli status                  # 预期 status=verified
```

| 步骤 | 预期 |
|---|---|
| `context` | `target_tests` 含 `test_price_filter_is_inclusive_and_keeps_order`；`regression_tests` 含另外 3 个 |
| `test --target`（改前） | `exit_code: 1`，失败原因是断言价格区间，不是 import 或环境错误 |
| `agent --replay --apply` | `applied: true`，`applied_by: git`，`demo_app/app.py` 内容 hash 改变 |
| `test --target --regression`（改后） | 两项 `exit_code: 0` |
| `status`（新进程） | `status: verified`，含目标/回归测试、两次退出码、代码版本 hash |

## 重置

```bash
bash scripts/reset.sh                     # 清 seekdb/PowerContext 与生成物
python -m minimax_context.cli restore before   # 恢复初始代码
```

## 规格变更验证（必做项三）

改写 `openspec/changes/add-price-filter/specs/book-search/spec.md` 任意内容
后执行 `cli ingest`，再 `cli context`：同一 `source_id` 的 `version` 变化，
输出使用新内容。`tests/test_retrieval.py::test_changed_spec_changes_the_selected_version`
自动化了这一点；`test_removed_spec_disappears_from_selection` 覆盖删除场景。

## 泛化验证（对照工程）

`fixtures/inventory/` 是一套无关工程（库存预约，不同领域/包名/中文
proposal）。把它当作工程根运行同一份代码：

```bash
cd fixtures/inventory
PYTHONPATH=<repo>/src python -m minimax_context.cli init
PYTHONPATH=<repo>/src python -m minimax_context.cli context
```

会得到 `stock/service.py`、`stock/tests/test_reservation.py` 以及它自己的
验收/回归测试划分，无需修改任何源码。对应自动化测试：
`tests/test_retrieval.py::test_second_project_needs_no_code_change`
与 `::test_english_task_over_the_second_project`。

## 已知限制

见 `TECH_NOTE.md` 末节。
