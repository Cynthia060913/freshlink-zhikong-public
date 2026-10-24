# C01 实际数据库数据字典（候选）
源码：`71e5a8820ee966db1b250afaf0a72143a4bdc62b`；33张业务/模板表，不计alembic_version。实际专属空库迁移后只读反射，不读取业务行。
ER分图只展示19个核心实体（A6/B6/C7），省略其它队列/冲突审计表不代表不存在；完整表字段和真实FK以下表为准。箭头从被引用父实体指向引用子实体，不承诺父方必须有子记录。
设备/观测的warehouse_id、C outbox的批次/来源、恢复冻结引用等UUID并不都存在数据库外键。不得根据字段名擅自补FK；业务真实性由权限/归属/同Connection门禁与回读验证承担。

## alerts_batch
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`code`|`VARCHAR(64)`|否||
|`warehouse_id`|`UUID`|否||
|`product`|`VARCHAR(120)`|否||
|`started_at`|`TIMESTAMP`|否||
|`ended_at`|`TIMESTAMP`|是||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- `warehouse_id` → `alerts_warehouse.id`
- 唯一约束：`warehouse_id,code`。

## alerts_batch_rule_snapshot
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`batch_id`|`UUID`|否||
|`version_id`|`UUID`|否||
|`rule_version`|`VARCHAR(64)`|是||
|`payload_json`|`VARCHAR`|否||
|`signature`|`VARCHAR(64)`|否||
|`effective_start_at`|`TIMESTAMP`|否||
|`effective_end_at`|`TIMESTAMP`|是||
|`supersedes_json`|`VARCHAR`|否||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- `batch_id` → `alerts_batch.id`
- 唯一约束：`batch_id,signature`。

## alerts_event
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`source_event_id`|`VARCHAR(128)`|否||
|`kind`|`VARCHAR(32)`|否||
|`batch_id`|`UUID`|是||
|`warehouse_id`|`UUID`|是||
|`device_id`|`VARCHAR(64)`|是||
|`metric_code`|`VARCHAR(64)`|是||
|`sampled_at`|`TIMESTAMP`|否||
|`value`|`DOUBLE PRECISION`|是||
|`unit`|`VARCHAR(8)`|是||
|`severity`|`VARCHAR(32)`|是||
|`observation_ids_json`|`VARCHAR(8192)`|是||
|`rule_snapshot_id`|`UUID`|是||
|`rule_version`|`VARCHAR(64)`|是||
|`algorithm_version`|`VARCHAR(64)`|是||
|`evidence_json`|`VARCHAR`|是||
|`content_digest`|`VARCHAR(71)`|否||
|`payload_json`|`VARCHAR`|否||
|`related_source_event_id`|`VARCHAR(128)`|是||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- `batch_id` → `alerts_batch.id`
- `warehouse_id` → `alerts_warehouse.id`

## alerts_notification_attempt
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`alert_event_id`|`UUID`|否||
|`channel`|`VARCHAR(32)`|否||
|`outcome`|`VARCHAR(32)`|否||
|`delivered`|`BOOLEAN`|否||
|`detail`|`VARCHAR(1000)`|是||
|`server_response`|`VARCHAR(1000)`|是||
|`retry_after_s`|`DOUBLE PRECISION`|是||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- `alert_event_id` → `alerts_event.id`

## alerts_status_transition
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`alert_event_id`|`UUID`|否||
|`from_status`|`VARCHAR(12)`|否||
|`to_status`|`VARCHAR(12)`|否||
|`trigger_source_event_id`|`VARCHAR(128)`|是||
|`reason`|`VARCHAR(128)`|是||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- `alert_event_id` → `alerts_event.id`

## alerts_warehouse
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`code`|`VARCHAR(64)`|否||
|`name`|`VARCHAR(120)`|否||
|`location`|`VARCHAR(200)`|是||
|`created_at`|`TIMESTAMP`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_command
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`command_id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`actor_id`|`UUID`|否||
|`action`|`VARCHAR(32)`|否||
|`enabled`|`BOOLEAN`|否||
|`lifetime_seconds`|`INTEGER`|否||
|`request_fingerprint`|`VARCHAR(64)`|否||
|`payload_text`|`VARCHAR(4096)`|否||
|`issued_at`|`TIMESTAMP`|否||
|`expires_at`|`TIMESTAMP`|否||
|`status`|`VARCHAR(16)`|否||
|`attempt_count`|`INTEGER`|否||
|`lease_id`|`UUID`|是||
|`lease_until`|`TIMESTAMP`|是||
|`next_retry_at`|`TIMESTAMP`|是||
|`transport_accepted_at`|`TIMESTAMP`|是||
|`ack_fingerprint`|`VARCHAR(64)`|是||
|`ack_received_at`|`TIMESTAMP`|是||
|`terminal_at`|`TIMESTAMP`|是||
|`failure_reason`|`VARCHAR(64)`|是||

数据库外键：
- `device_id` → `iot_device.id`

## iot_command_attempt
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`attempt_id`|`UUID`|否|是|
|`command_id`|`UUID`|否||
|`started_at`|`TIMESTAMP`|否||
|`completed_at`|`TIMESTAMP`|是||
|`outcome`|`VARCHAR(16)`|否||

数据库外键：
- `command_id` → `iot_command.command_id`

## iot_command_receipt
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`command_id`|`UUID`|否|是|
|`fingerprint`|`VARCHAR(64)`|否|是|
|`received_at`|`TIMESTAMP`|否||
|`executed_at`|`TIMESTAMP`|是||
|`result`|`VARCHAR(16)`|否||
|`cooling_enabled`|`BOOLEAN`|否||
|`reason`|`VARCHAR(128)`|是||
|`outcome`|`VARCHAR(16)`|否||

数据库外键：
- `command_id` → `iot_command.command_id`

## iot_connectivity_event
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`source_event_id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|是||
|`occurred_at`|`TIMESTAMP`|否||
|`kind`|`VARCHAR(16)`|否||
|`reason`|`VARCHAR(64)`|否||
|`related_source_event_id`|`UUID`|是||
|`delivery_state`|`VARCHAR(16)`|否||

数据库外键：
- `device_id` → `iot_device.id`

## iot_device
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`code`|`VARCHAR(64)`|否||
|`name`|`VARCHAR(120)`|否||
|`kind`|`VARCHAR(32)`|否||
|`warehouse_id`|`UUID`|是||
|`is_simulated`|`BOOLEAN`|否||
|`created_at`|`TIMESTAMP`|否||
|`last_seen_at`|`TIMESTAMP`|是||
|`connectivity`|`VARCHAR(16)`|否||
|`last_sent_at`|`TIMESTAMP`|是||
|`last_heartbeat_message_id`|`UUID`|是||
|`offline_source_event_id`|`UUID`|是||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_event_delivery_checkpoint
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`source_event_id`|`UUID`|否|是|
|`fingerprint`|`VARCHAR(64)`|否||
|`state`|`VARCHAR(16)`|否||
|`attempt_count`|`INTEGER`|否||
|`next_attempt_at`|`TIMESTAMP`|否||
|`lease_id`|`UUID`|是||
|`lease_until`|`TIMESTAMP`|是||
|`last_reason`|`VARCHAR(64)`|否||
|`target_event_id`|`UUID`|是||
|`completed_at`|`TIMESTAMP`|是||
|`manual_retry_count`|`INTEGER`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_event_delivery_retry_audit
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`source_event_id`|`UUID`|否||
|`actor_id`|`UUID`|否||
|`recorded_at`|`TIMESTAMP`|否||
|`cycle`|`INTEGER`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_heartbeat_conflict
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`device_id`|`UUID`|否|是|
|`message_id`|`UUID`|否|是|
|`fingerprint`|`VARCHAR(64)`|否|是|
|`received_at`|`TIMESTAMP`|否||

数据库外键：
- `device_id` → `iot_device.id`

## iot_heartbeat_message
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`device_id`|`UUID`|否|是|
|`message_id`|`UUID`|否|是|
|`fingerprint`|`VARCHAR(64)`|否||
|`outcome`|`VARCHAR(32)`|否||
|`received_at`|`TIMESTAMP`|否||

数据库外键：
- `device_id` → `iot_device.id`

## iot_stream_admission
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`reception_id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`stream_epoch`|`UUID`|否||
|`admission_seq`|`BIGINT`|否||
|`fingerprint`|`VARCHAR(64)`|否||
|`outcome`|`VARCHAR(32)`|否||
|`observation_id`|`UUID`|是||

数据库外键：
- `device_id,stream_epoch` → `iot_stream_frontier.device_id,stream_epoch`
- 唯一约束：`device_id,stream_epoch,admission_seq`。

## iot_stream_frontier
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`device_id`|`UUID`|否|是|
|`stream_epoch`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`origin_verified`|`BOOLEAN`|否||
|`admitted_seq`|`BIGINT`|否||
|`settled_through`|`BIGINT`|否||
|`evidence_revision`|`BIGINT`|否||
|`gap_reason`|`VARCHAR(64)`|否||

数据库外键：
- 无；同名UUID不能画成外键。
- 唯一约束：`device_id,stream_epoch`。

## iot_telemetry_inbox
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`reception_id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`received_at`|`TIMESTAMP`|否||
|`raw_text`|`VARCHAR`|否||
|`payload_bytes`|`INTEGER`|否||
|`payload_sha256`|`VARCHAR(64)`|否||
|`fingerprint`|`VARCHAR(64)`|否||
|`state`|`VARCHAR(16)`|否||
|`attempt_count`|`INTEGER`|否||
|`next_attempt_at`|`TIMESTAMP`|否||
|`lease_id`|`UUID`|是||
|`lease_until`|`TIMESTAMP`|是||
|`last_reason`|`VARCHAR(64)`|否||
|`manual_retry_count`|`INTEGER`|否||
|`last_retry_actor`|`UUID`|是||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_telemetry_ingest_receipt
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`reception_id`|`UUID`|否|是|
|`fingerprint`|`VARCHAR(64)`|否||
|`outcome`|`VARCHAR(32)`|否||
|`observation_id`|`UUID`|是||
|`reason`|`VARCHAR(64)`|否||
|`completed_at`|`TIMESTAMP`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_telemetry_ingress_audit
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`recorded_at`|`TIMESTAMP`|否||
|`device_id`|`UUID`|是||
|`reception_id`|`UUID`|是||
|`reason`|`VARCHAR(64)`|否||
|`payload_bytes`|`BIGINT`|否||
|`payload_sha256`|`VARCHAR(64)`|否||
|`actor_id`|`UUID`|是||

数据库外键：
- 无；同名UUID不能画成外键。

## iot_telemetry_queue_guard
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`INTEGER`|否|是|

数据库外键：
- 无；同名UUID不能画成外键。

## item
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`description`|`VARCHAR(255)`|是||
|`title`|`VARCHAR(255)`|否||
|`id`|`UUID`|否|是|
|`owner_id`|`UUID`|否||
|`created_at`|`TIMESTAMP`|是||

数据库外键：
- `owner_id` → `user.id`

## telemetry_candidate_outbox_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`source_event_id`|`UUID`|否|是|
|`related_source_event_id`|`UUID`|是||
|`frozen_device_id`|`UUID`|是||
|`frozen_warehouse_id`|`UUID`|是||
|`frozen_stream_epoch`|`UUID`|是||
|`frozen_admitted_seq`|`INTEGER`|是||
|`frozen_settled_through`|`INTEGER`|是||
|`frozen_evidence_revision`|`INTEGER`|是||
|`observation_id`|`UUID`|否||
|`body_sha256`|`VARCHAR(64)`|否||
|`body`|`JSON`|否||
|`state`|`VARCHAR(24)`|否||
|`attempt_count`|`INTEGER`|否||
|`next_attempt_at_utc`|`TIMESTAMP`|否||
|`lease_token`|`UUID`|是||
|`lease_until_utc`|`TIMESTAMP`|是||
|`last_error_code`|`VARCHAR(80)`|是||

数据库外键：
- `observation_id` → `telemetry_observation_candidate.id`

## telemetry_candidate_outbox_conflict_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`source_event_id`|`UUID`|否||
|`observation_id`|`UUID`|否||
|`body_sha256`|`VARCHAR(64)`|否||
|`body`|`JSON`|否||
|`first_detected_at_utc`|`TIMESTAMP`|否||

数据库外键：
- `source_event_id` → `telemetry_candidate_outbox_candidate.source_event_id`
- 唯一约束：`source_event_id,observation_id,body_sha256`。

## telemetry_evaluation_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`observation_id`|`UUID`|否|是|
|`state`|`VARCHAR(24)`|否||

数据库外键：
- `observation_id` → `telemetry_observation_candidate.id`

## telemetry_observation_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`message_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`content_sha256`|`VARCHAR(64)`|否||
|`raw_text`|`VARCHAR`|否||
|`raw_payload`|`JSON`|否||
|`sampled_at_utc`|`TIMESTAMP`|是||
|`first_received_at_utc`|`TIMESTAMP`|否||
|`normalized`|`JSON`|否||
|`quality`|`JSON`|否||
|`risk`|`JSON`|否||

数据库外键：
- 无；同名UUID不能画成外键。
- 唯一约束：`device_id,message_id`。

## telemetry_quality_tag_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`observation_id`|`UUID`|否|是|
|`tag`|`VARCHAR(128)`|否|是|

数据库外键：
- `observation_id` → `telemetry_observation_candidate.id`

## telemetry_reception_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`observation_id`|`UUID`|是||
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`received_at_utc`|`VARCHAR(35)`|否||
|`raw_text`|`VARCHAR`|否||
|`receipt_sha256`|`VARCHAR(64)`|否||
|`outcome`|`VARCHAR(32)`|否||
|`reason`|`VARCHAR(80)`|否||

数据库外键：
- `observation_id` → `telemetry_observation_candidate.id`

## telemetry_reception_reuse_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`claimed_reception_id`|`UUID`|否||
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`received_at_utc`|`VARCHAR(35)`|否||
|`raw_text`|`VARCHAR`|否||
|`receipt_sha256`|`VARCHAR(64)`|否||

数据库外键：
- `claimed_reception_id` → `telemetry_reception_candidate.id`
- 唯一约束：`claimed_reception_id,receipt_sha256`。

## telemetry_recovery_progress_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`original_source_event_id`|`UUID`|否|是|
|`device_id`|`UUID`|否||
|`warehouse_id`|`UUID`|否||
|`batch_id`|`UUID`|否||
|`anomaly_sampled_at_utc`|`VARCHAR(35)`|否||
|`comparison`|`VARCHAR(2)`|否||
|`threshold_c`|`DOUBLE PRECISION`|否||
|`min_samples`|`INTEGER`|否||
|`max_gap_seconds`|`INTEGER`|否||
|`last_sampled_at_utc`|`VARCHAR(35)`|是||
|`last_received_at_utc`|`VARCHAR(35)`|是||
|`last_received_observation_id`|`UUID`|是||
|`last_matching_at_utc`|`VARCHAR(35)`|是||
|`observation_ids`|`JSON`|否||
|`eligible`|`BOOLEAN`|否||
|`version`|`INTEGER`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## telemetry_snapshot_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`id`|`UUID`|否|是|
|`actor_id`|`UUID`|否||
|`filters_sha256`|`VARCHAR(64)`|否||
|`policy_version`|`VARCHAR(64)`|否||
|`created_at_utc`|`TIMESTAMP`|否||
|`expires_at_utc`|`TIMESTAMP`|否||
|`member_count`|`INTEGER`|否||

数据库外键：
- 无；同名UUID不能画成外键。

## telemetry_snapshot_member_candidate
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`snapshot_id`|`UUID`|否|是|
|`ordinal`|`INTEGER`|否|是|
|`observation_id`|`UUID`|是||
|`projection`|`JSON`|否||

数据库外键：
- `observation_id` → `telemetry_observation_candidate.id`
- `snapshot_id` → `telemetry_snapshot_candidate.id`

## user
|字段|数据库类型|可为空|主键|
|---|---|---|---|
|`email`|`VARCHAR(255)`|否||
|`is_active`|`BOOLEAN`|否||
|`is_superuser`|`BOOLEAN`|否||
|`full_name`|`VARCHAR(255)`|是||
|`hashed_password`|`VARCHAR`|否||
|`id`|`UUID`|否|是|
|`created_at`|`TIMESTAMP`|是||

数据库外键：
- 无；同名UUID不能画成外键。
