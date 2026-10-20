# A 供 M03 总 ER 核对的源码结构底稿

2026-10-20。**只读源码元数据证据，不是正式数据库实况、最终 ER 图或迁移批准。**
精确取源`895d257c93d55378dbee64271e3a7df7de4e9dc3`（C业务原样e6bb8ef，B业务8561ce1）。
在既有缓存容器、无网络、只读backend挂载下导入模型并收集声明，未连接数据库、
未执行DDL、未读取记录/配置秘密，也未把用户原业务库当测试对象。

同一源码快照共 **31张声明表、21条显式外键**：模板2、A已发布源码模型7、B候选6、
A04非活动候选6、C候选10。这个总数不是“main已发布31表”或“本机原库已有31表”。
C存储/快照/outbox共用metadata九表，恢复进度另一个metadata一表，不能漏掉后者。

## 表、主键和真实声明的关系

箭头只表示源代码显式ForeignKey。无箭头项即使有同名UUID字段，也不能画成已有
物理外键；业务引用请另画虚线并由作者确认。复合主键字段按源码顺序记录。

| 归属／发布层次 | 表 | 主键 | 显式外键（列 → 目标） |
|---|---|---|---|
| 模板 | user | id | 无 |
| 模板 | item | id | owner_id → user.id |
| A01源码 | iot_device | id | 无；warehouse_id不是B库外键 |
| A02源码 | iot_heartbeat_message | device_id,message_id | device_id → iot_device.id |
| A02源码 | iot_heartbeat_conflict | device_id,message_id,fingerprint | device_id → iot_device.id |
| A02源码 | iot_connectivity_event | source_event_id | device_id → iot_device.id |
| A03源码 | iot_command | command_id | device_id → iot_device.id |
| A03源码 | iot_command_attempt | attempt_id | command_id → iot_command.command_id |
| A03源码 | iot_command_receipt | command_id,fingerprint | command_id → iot_command.command_id |
| B01候选 | alerts_warehouse | id | 无 |
| B01候选 | alerts_batch | id | warehouse_id → alerts_warehouse.id |
| B01候选 | alerts_batch_rule_snapshot | id | batch_id → alerts_batch.id |
| B01候选 | alerts_event | id | warehouse_id → alerts_warehouse.id；batch_id → alerts_batch.id |
| B01候选 | alerts_notification_attempt | id | alert_event_id → alerts_event.id |
| B01候选 | alerts_status_transition | id | alert_event_id → alerts_event.id |
| A04非活动候选 | iot_telemetry_queue_guard | id | 无；id=1种子必须保留 |
| A04非活动候选 | iot_telemetry_inbox | reception_id | 无 |
| A04非活动候选 | iot_telemetry_ingest_receipt | reception_id | 无 |
| A04非活动候选 | iot_telemetry_ingress_audit | id | 无 |
| A04非活动候选 | iot_event_delivery_checkpoint | source_event_id | 无；来源事件为业务引用 |
| A04非活动候选 | iot_event_delivery_retry_audit | id | 无；source_event_id为业务引用 |
| C候选 | telemetry_observation_candidate | id | 无；device/warehouse为可信业务标识 |
| C候选 | telemetry_quality_tag_candidate | observation_id,tag | observation_id → telemetry_observation_candidate.id |
| C候选 | telemetry_reception_candidate | id | observation_id → telemetry_observation_candidate.id |
| C候选 | telemetry_reception_reuse_candidate | id | claimed_reception_id → telemetry_reception_candidate.id |
| C候选 | telemetry_evaluation_candidate | observation_id | observation_id → telemetry_observation_candidate.id |
| C候选 | telemetry_candidate_outbox_candidate | source_event_id | observation_id → telemetry_observation_candidate.id |
| C候选 | telemetry_candidate_outbox_conflict_candidate | id | source_event_id → telemetry_candidate_outbox_candidate.source_event_id |
| C候选 | telemetry_snapshot_candidate | id | 无；actor_id为业务引用 |
| C候选 | telemetry_snapshot_member_candidate | snapshot_id,ordinal | snapshot_id → telemetry_snapshot_candidate.id；observation_id → telemetry_observation_candidate.id |
| C候选／独立metadata | telemetry_recovery_progress_candidate | original_source_event_id | 无；device/warehouse/batch/原异常为业务引用 |

## 成图前不能省略的限制

1. A03源码已发布与原业务库是否升级分别核对。B01→A04→C唯一正式链尚未发布，
   本底稿不激活候选、不另开head，也不把源metadata代替Alembic/真实PG比对。
2. A命令多次尝试/多种回执，B事件多次通知尝试/状态变更，C观测多次接收，均不应
   误画成一对一。一个observation的质量标签复合键不等于单字符串风险结论。
3. B历史规则快照的触发器/版本区间与只读约束需同时读B01迁移，不能仅凭模型图
   宣称数据库能保持不可变。批次(warehouse_id,code)唯一，设备message_id去重为
   (device_id,message_id)，源事件去重/正文摘要语义由F03及模块作者复核。
4. 用户、设备、仓库、批次、actor、原异常等业务引用不全是数据库外键。不能为让图
   好看而增加FK、删除级联或改字段；尤其历史证据不因删设备就自行宣称可删除。
5. A24接纳序号／连续终态前沿仍是提案，**没有对应已实现表**；不得凭该文档把
   新表画进“实际系统”。新增字段必须经共同审核和唯一迁移安排后再更新本表。

## 各作者复核与最终替换

A提供自身13表（已发布源码7＋非活动6）的说明；B请复核6表/FK/触发器与业务引用；
C请复核10表、恢复进度独立metadata、快照成员和坏接收留痕。A不会代两位签收。
本底稿可现在供B汇编需求/架构/ER说明及PPT结构，不用等27日从零整理。

最终M03必须从同一冻结发布SHA的**正式迁移及实际PG inspection**重取：表/列/主键/
FK/索引/检查约束/种子，逐项与源模型比较，再生成最终ER图；不能只改标题成“最终”。
恢复前沿、完整E1、两通知、14设备、第二机与材料签收继续按#5/#6/#12真实证据。
