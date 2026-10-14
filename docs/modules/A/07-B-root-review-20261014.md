# A/B 根接线及 PR10 复核

2026-10-14。共同 main `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813`；
B 源码 `58546e9993381b92d158cb437a31d61b95dfb1c1`；
C 源码 `6f38c1cda4442ebe868720791fff0616731f2397`。

## 已完成的 A 侧准备

独立本地分支 `feature/a-b-integration-review`，不改 B 远程分支或 main。
在根 router 挂 B 聚合器，外层统一加 `/api/v1`。写入候选及设备事件只准
管理员，普通活跃用户可查询。服务凭据尚待正式实现，不用放开普通用户代替。

运行于缓存 `freshlink-app:p0`（Python 3.14）临时容器，禁网络、只读源码、
内存 SQLite。B 原有 185 单测实际通过；新增 8 项 API 路由/权限检查实际通过。
未使用本机 D 盘 Python，未改主数据库。Ruff 与 diff 检查通过。

## 不能批准 PR10 的实际边界问题

1. `models/event.py::_require_aware` 用于 before validator，仅接受 datetime。
   正常 JSON `sampled_at="2026-10-14T00:00:00Z"` 在候选和设备事件均拒绝。
   B 现有 datetime 直构造测试没覆盖 HTTP JSON；应在 Pydantic 解析后校验。
2. observation IDs 等追溯字段被现有 CandidateCreate 忽略。两份仅观测依据
   不同的输入得到相同摘要，无法满足同键不同证据冲突及完整追溯。
3. `receive_candidate` 的读阶段数据库 OperationalError 直接逃逸，不产生
   契约约定的 retryable_failure。其他读/refresh 阶段也须补回归。
4. 三版开放区间 S1→S2→S3（S2 supersedes S1，S3 supersedes S2）实际在
   第三次插入抛 SnapshotOverlapConflict，与消费端派生历史区间的目标不符。

作者负责修 B 模块，A 本轮没有修改 B 的 DTO/服务。F03 RC2 单批扩充提案
见 contracts/F03-RC2.md，三方对同一提交签收后再称正式契约。

## 未测与下一步

本轮不是 PostgreSQL 迁移/事务并发、前端、MQTT、两通知实收或 E1 验收。
B 分支尚不含最新 main（22 behind/21 ahead），先安全 merge 最新 main 并
保留成果；修上述边界、提供正式迁移与通知评估管道。C 继续原始观测入库。
A 后续接生产心跳/控制与 C 服务，再按一库小场景联调，不关闭 S0/E1。
