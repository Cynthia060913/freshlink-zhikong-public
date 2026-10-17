# B：最小业务接口提案（阶段一提交）

> 落地位置：`docs/modules/B/01-contract-proposal.md`（共同仓库 `feature/b-notification-bootstrap` 分支）。
> 严格遵循 `contracts/INTERFACES.md` v0.1 字段，**不发明**新字段；待确认字段单独用 `?` 标注。
> 阶段一交付物：本提案 + 通知适配器骨架 + 单元测试；service/routes/迁移留到 B1 阶段。

## 0. 元信息

- 角色：B
- 日期：2026-10-10
- 输入：`contracts/INTERFACES.md` v0.1（44 行）+ A01 `Device` schema（`backend/app/modules/iot/models.py`）
- 输出目标：A 本对话 Agent 形成"版本化契约 + 样例 + 测试"提交；当前 B 仅做最小签名与样例

## 1. 模块路径（与 INTERFACES / v4 一致）

| 模块 | 路径 | 状态 |
|---|---|---|
| B 仓储 | `backend/app/modules/warehouse/` | **未建**（B1 阶段） |
| B 告警 | `backend/app/modules/alerts/` | **已建**（本轮只建 notifications 子模块） |
| 前端 | `frontend/src/features/warehouse/`、`frontend/src/features/alerts/` | 未建（公共导航由 A 接线） |
| B 文档 | `docs/modules/B/`、`docs/modules/B/integration-notes.md` | 已建 |

INTERFACES 草案第 22 行亦建议相同模块路径，与 v4 第 5 章一致。

## 2. 共享单位与时间基线（沿用 INTERFACES 第 5 行）

- **ID**：UUID（A01 后端 `Device.id: uuid.UUID` 已对齐）
- **时区**：UTC ISO 8601 带时区（`+00:00`）；页面端由前端展示为北京时间（Asia/Shanghai）
- **采样时刻 / 接收时刻**：分两个字段保留；模拟加速单独记录（**?** 是否新增 `simulated_speed` 字段，需 A/C 决定；B 不发明）
- **单位**：温度 °C、相对湿度 %RH、功率 W、电量 kWh
- **质量**：原始值保留 + 处理质量标记（`sufficient / missing / invalid`）；低数据不等同低风险

## 3. B 服务签名（最小提案；service.py 在 B1 阶段才落盘）

### 3.1 B 接收：设备事件（A→B）

```text
Function: receive_device_event(event: DeviceEventIn) -> ReceiveResult
Caller:  A 设备模块在确认设备身份后异步调用
落点:    backend/app/modules/alerts/service.py::receive_device_event (B1 阶段)
```

`DeviceEventIn`（字段全部来自 INTERFACES 第 8 行 "设备事件"）：

| 字段 | 类型 | 来源 | 必填 | 校验 / 说明 |
|---|---|---|---|---|
| `source_event_id` | UUID | A | 是 | 幂等键；重复返回已存在状态，不重新触发 |
| `device_id` | UUID | A | 是 | 对应 `iot_device.id`（A01 已 UUID） |
| `warehouse_id` | UUID \| null | A | 是（设备未绑可 null） | A01 当前 `Device.warehouse_id` 硬拒；B 不绕过，由 A 在后续开放 |
| `occurred_at` | ISO8601 UTC | A | 是 | 服务接收时刻由 B 自己记 `received_at`（不引入 INTERFACES 字段） |
| `kind` | enum | A | 是 | `"offline" \| "recovered" \| "fault"` |
| `reason` | str | A | 否 | 长度 ≤ 256（**?** A 复审） |

样例输入：

```json
{
  "source_event_id": "5e3b1c10-7e9c-4a1f-9b6a-8c2e0a5e7b71",
  "device_id": "8c1f4a9c-2b8e-4f8c-9a91-1d6b8a7f9c30",
  "warehouse_id": null,
  "occurred_at": "2026-10-10T07:30:00.000+00:00",
  "kind": "offline",
  "reason": "heartbeat timeout 120s"
}
```

返回 `ReceiveResult`：

| 状态 | 含义 |
|---|---|
| `accepted_new` | 首次入库，已触发下游（告警生成） |
| `accepted_duplicate` | `source_event_id` 已存在；幂等通过，未重新触发 |
| `invalid` | 字段校验失败（含 reason 长度超限、设备不存在等） |

失败语义：

- **不抛异常到调用方**。失败通过结构化 `ReceiveResult` 返回（可追溯、可重试）。
- 设备不存在返回 `invalid`（不是 404，避免 A 模块与 B 模块相互渗透）。
- **服务接受 ≠ 通知已发**。通知适配器在 B 第二轮单独接入。

### 3.2 B 接收：风险候选（C→B）

```text
Function: receive_risk_candidate(candidate: RiskCandidateIn) -> ReceiveResult
Caller:  C 阈值/模型模块基于已存储观测异步调用
落点:    backend/app/modules/alerts/service.py::receive_risk_candidate (B1 阶段)
```

`RiskCandidateIn`（字段全部来自 INTERFACES 第 11 行 "数据风险候选"）：

| 字段 | 类型 | 来源 | 必填 | 校验 / 说明 |
|---|---|---|---|---|
| `source_event_id` | UUID | C | 是 | 幂等键，同 3.1 |
| `observation_ids` | UUID[] | C | 是 | 非空；引用 C 原始观测主键 |
| `warehouse_id` | UUID | C | 是 | |
| `batch_ids` | UUID[] | C | 否 | B 提供批次规则后 C 关联 |
| `occurred_at` | ISO8601 UTC | C | 是 | |
| `kind` | enum | C | 是 | **?** C 提枚举（与 A 设备事件 kind 需约定映射） |
| `severity` | enum | C | 是 | `"info" \| "warning" \| "critical"`（**?** 待 C 确认，仅作候选枚举） |
| `evidence` | object | C | 是 | C 提供可追溯字段（不发明） |
| `algorithm_version` | str | C | 是 | semver；不接受空串；写明 C 算法/规则版本 |

样例输入：

```json
{
  "source_event_id": "ab12cd34-ef56-7890-aabb-ccddeeff0011",
  "observation_ids": ["11111111-2222-3333-4444-555555555555"],
  "warehouse_id": "7d4a9c2e-1a3b-4c8d-9e0f-2a3b4c5d6e7f",
  "batch_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
  "occurred_at": "2026-10-10T07:31:00.000+00:00",
  "kind": "temperature_high",
  "severity": "warning",
  "evidence": {
    "metric": "temperature_c",
    "value": 6.4,
    "unit": "°C",
    "threshold": "rule_v1:temperature_c>4.0;duration>15min",
    "duration_s": 1800
  },
  "algorithm_version": "threshold-rule/0.1.0"
}
```

### 3.3 B 提供：批次与规则版本快照（B→C 只读）

```text
Function: get_batch_rule_snapshot(
    as_of: datetime | None = None,
    warehouse_id: UUID | None = None,
    batch_id: UUID | None = None,
) -> list[BatchRuleSnapshot]
Caller:  C 阈值评估；前端 / A / B 自检
落点:    backend/app/modules/warehouse/service.py::get_batch_rule_snapshot (B1 阶段)
```

`BatchRuleSnapshot`（字段全部来自 INTERFACES 第 12 行 "批次与规则快照"）：

| 字段 | 类型 | 来源 | 必填 | 说明 |
|---|---|---|---|---|
| `batch_id` | UUID | B | 是 | |
| `warehouse_id` | UUID | B | 是 | |
| `crop_variety` | str | B | 是 | INTERFACES 写"品种" |
| `stored_at` | ISO8601 UTC | B | 是 | 入库时刻 |
| `out_at` | ISO8601 UTC \| null | B | 否 | 未出库为 null |
| `rule_version` | str | B | 是 | semver；规则范围唯一指针 |
| `rule_scope` | object | B | 是 | 适用阈值范围 |
| `source` | str | B | 是 | 规则原始配置身份；不接受空串 |

无规则响应：

```json
{"rules": [], "hint": "no rules configured; please configure rule for this batch"}
```

`hint` 是 B 提供给前端的提示文案，**它不在 INTERFACES 字段里**，仅展示。要 **?** 三方 review。

### 3.4 B 提供：告警 / 通知查询（A→B 同读 / 页面→B）

```text
Function: list_alerts(
    *, since: datetime, until: datetime,
    alert_id: UUID | None = None,
    source: enum | None = None,
    severity: enum | None = None,
    limit: int = 100, cursor: str | None = None,
) -> AlertPage
Function: get_alert(alert_id: UUID) -> AlertDetail
Caller:  前端 / A 控制回执显示 / E1 追溯
落点:    backend/app/modules/alerts/service.py::list_alerts / get_alert (B1 阶段)
```

返回字段最小集：

- `alert_id: UUID`
- `source_event_id: UUID`（去重键外键）
- `device_id | warehouse_id | batch_ids`
- `observed_value: object`（引用 C 原始观测 / A 设备事件，不可伪造）
- `severity: enum`
- `state: enum`（INTERFACES 第 18 行 `open / acknowledged / resolved / closed`）
- `first_seen_at: ISO8601`
- `last_retry_at: ISO8601 | null`
- `notification_dispatch: object`（候选第三轮；本轮不暴露此对象）

## 4. 通知适配器契约（本轮已落）

```text
Abstract: NotificationTransport(kind).send(envelope) -> NotificationResult
落点:     backend/app/modules/alerts/notifications/transport.py
```

**4 个结果分类**（按 `02-B-Agent指令.md` 第 17 行要求）：

| Outcome | 含义 | 触发 |
|---|---|---|
| `accepted_by_service` | 上游服务接受 | smtplib 收 250 / APNs/FCM HTTP 202 |
| `transient_failure` | 暂时失败，可重试 | 4xx SMTP 收件人不存在以外的其它错误、连接拒绝、`TransientPushError` |
| `permanent_failure` | 永久失败，不重试 | 4xx SMTP 收件人不存在、`PermanentPushError`、`NotConfigured`、空收件人 |
| `timeout` | 超时，可重试 | `smtplib.SMTP` 超时 |

`NotificationResult.delivered=True` **只证明上游服务接受了**，**不证明终端实收**。INTERFACES 第 18 行明确"Broker 收到不等于设备执行"——同原则推广到通知：B agent 必须**分别**记录"服务接受"和"终端实收"，不能宣称"两渠道完成"。

实现：

| 文件 | 内容 |
|---|---|
| `notifications/transport.py` | 抽象与类型 |
| `notifications/smtp.py` | `SMTPTransport` + `SMTPConfig` + `MockSMTP` 工厂注入点 |
| `notifications/app_push.py` | `AppPushTransport` 抽象 + `DisabledAppPushTransport` 默认实现（无凭据时显式 `NotConfigured`） |

## 5. 幂等、失败与状态机

| 维度 | 规则 | 来源 |
|---|---|---|
| 幂等键 | 设备事件：`source_event_id`；风险候选：`source_event_id` | INTERFACES "按 source_event_id 去重" |
| 接收失败 | 通过结构化 `ReceiveResult` 返回；不抛未捕获异常 | v4 02-B-Agent 指令 |
| 通知发送 | "人工确认 ≠ 故障已恢复"；"Broker 收到 ≠ 设备执行" | INTERFACES 第 18 行 |
| 命令状态 | `queued → sent → acknowledged \| failed \| timeout`（B 不直接产生命令状态；由 A 提供只读引用）| INTERFACES 第 18 行 |
| 告警状态 | `open → acknowledged → resolved → closed` | INTERFACES 第 18 行 |
| 通知状态 | 本轮抽象为 `accepted_by_service / transient_failure / permanent_failure / timeout`；告警状态与通知状态分两路互引 | v4 02-B-Agent 指令 |
| 不静默丢 | 数据与风险事件关联保留，一次模块调用失败不可静默丢弃 | INTERFACES "不能因一次模块调用失败静默丢弃" |

## 6. 调用顺序

1. A 设备身份确认 → `alerts.service.receive_device_event`
2. C 阈值评估完成 → `alerts.service.receive_risk_candidate`
3. C 评估前/中 → `warehouse.service.get_batch_rule_snapshot`
4. 通知适配器 → `notifications.dispatch(envelope)` → SMTP / APP（接收人授权与实收分别记录）
5. 前端 / 追溯 / A 控制 → `alerts.service.list_alerts / get_alert`
6. A 控制回执的引用 → A 写 `command_id`；B 在告警详情附 `command_id` 只读字段；B 不创建不更新控制记录

## 7. 失败样例 / 边界

- **重复事件**：相同 `source_event_id` 第二次 → `accepted_duplicate`
- **观测引用失效**：C 传 `observation_ids` 但 ID 不存在 → `invalid` / `invalid_reason="observation_not_found"`
- **规则未配置**：`get_batch_rule_snapshot` 返回 `{rules: [], hint: ...}`；C 自行跳过阈值评估，**B 不自造阈值**（INTERFACES 第 42 行）
- **`reason` 长度超 256** → `invalid` / `invalid_reason="reason_too_long"`
- **未知 `kind`** → `invalid` / `invalid_reason="unknown_kind"`
- **设备不存在**：返回 `invalid`，`invalid_reason="device_unknown"`；**不**返回 404

## 8. 待三方共同确认（**?** 汇总）

1. `reason` 字段上限；`kind` / `severity` 枚举；`algorithm_version` 命名规范
2. `hint` 字段是否入 INTERFACES（仅前端文案）
3. `notification_dispatch` 对象的最小字段集（B 第三轮）
4. A 设备事件 `kind` 与 C 风险候选 `kind` 是否共用枚举（v4 第 40–41 行分别枚举，可能需要映射）→ 三方 review
5. A01 当前 `Device.warehouse_id` 硬拒（`models.py` 第 40 行）→ A 在 B 阶段一后解封；B 不绕过
6. `simulated_speed` 是否新增（v4 提到"模拟加速单独记录"）

## 9. B 不会做（在本提案范围内）

- 不建 HTTP 路由（由指定集成 Agent 处理；见 `integration-notes.md`）
- 不建持久化模型 / 迁移（待迁移轮到 B，且三方确认后）
- 不自造 INTERFACES 之外字段
- 不修改 iot、telemetry、analytics、共享路由或导航
- 不发任何外部通知（无授权）

## 10. 阶段一交付物

| 路径 | 内容 |
|---|---|
| `backend/app/modules/alerts/notifications/transport.py` | 抽象 + `NotificationEnvelope` / `NotificationResult` / `DeliveryOutcome` |
| `backend/app/modules/alerts/notifications/smtp.py` | SMTP 实现 + `SMTPConfig` + 注入式工厂 |
| `backend/app/modules/alerts/notifications/app_push.py` | APP 抽象 + Disabled 默认实现（无 token 显式 `NotConfigured`） |
| `backend/tests/modules/alerts/test_transport.py` | 契约测试 |
| `backend/tests/modules/alerts/test_smtp.py` | 4 类 SMTP 测试 |
| `backend/tests/modules/alerts/test_app_push.py` | APP 推送测试 |

测试命令：`cd backend && python -m unittest discover -t . -s tests/modules/alerts -p 'test_*.py' -v`

所有测试要求 4 类覆盖且不依赖 alembic / SQLModel / 数据库。
