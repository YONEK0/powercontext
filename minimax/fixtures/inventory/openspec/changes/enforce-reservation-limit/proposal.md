## Why

仓库目前只能查看库存，无法真正占用货物：`reserve` 总是返回 `accepted: true`，
即使申请数量超过可用量。缺少这个检查，超卖无法被发现。

## What Changes

- `reserve` 依据 `free_quantity` 判断能否接受，超出可用量时返回 `accepted: false`。
- 拒绝时保持已存储的 `reserved` 数值不变。
- 已接受时把 `reserved` 增加申请数量。
- 不改变 `available`、`low_stock`、仓库过滤的现有行为。

## Capabilities

### Modified Capabilities

- `stock`: 预约需要校验可用量，拒绝超量申请。

## Impact

- `stock/service.py`：`reserve` 的可用量校验。
- `stock/tests/test_reservation.py`：目标测试与回归测试。

## Non-Goals

- 并发锁、跨仓调拨、预约过期时间。
