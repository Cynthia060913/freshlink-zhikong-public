# A66：通知前只读库存预检

这是当前main0e之后的独立工具候选，不自动进入应用归档，不改B派发器、
通知配置格式或恢复协议，也不授权发送。真实通知及最终投稿仍分别批准。

## 为什么需要

B材料候选b952的预检SQL包含`alerts_event.status`，但真实表没有该字段；
生命周期状态由追加的transition推导。材料将退出码1也列为可通过，而当前
notification_service在租约/存储中断时返回1，应当阻断发送。
`max_events_per_cycle=1`仅限制数量，不限定来源；不能凭pending_count=1
推断选中了批准的事件，更不能清空旧库来凑出单事件场景。

## 用法与结果

在当前受控容器/隔离项目环境、已配置的**新建专属E1库**中，从backend运行：

```text
python -m app.modules.iot.notification_preflight --config <本机私密收件配置> --source-event-id <拟批准来源>
```

配置仍使用notification_service已有的严格四字段JSON；本次E1限定一个收件人、
一个事件、每渠道一次。渠道秘密按现有适配器的本机配置读取，不写进JSON。
工具不会打印连接串、邮箱、Webhook、密钥、事件正文或异常细节。

工具先识别恰好SMTP/飞书两渠道（smtp_email/app_push），再用真实PostgreSQL
REPEATABLE READ只读事务检查：库内恰好一条事件、来源匹配、零既有attempt、
B实际待发扫描确实只有该事件。额外事件/旧attempt一律阻断，要求人工复核，
不删数据、不自动“修复”库存。不会调用send、派发worker、迁移或写attempt。

- 退出0：`readiness_only`，只证明这一时刻库存/配置形状一致。
- 退出1：`blocked`，有库存/来源/旧attempt问题，不能当预检通过。
- 退出2：配置/渠道/存储不可用或输入错误，只给固定脱敏错误。
- `send_authorized=false`和`terminal_receipt_confirmed=false`恒为false。

预检不是发送许可，也不是原子发送门禁。结束后库存可能变化；正式试验必须
由A/B协调暂停其他写入/发送，重新核准同一来源、版本、收件范围和次数。
如果不能保证范围，就停在预检，不能直接把--send接到本工具后面。

## 验证边界

回归使用专属临时PostgreSQL和随机schema，证明单来源、额外更早事件、
原SQL错误、旧attempt/错误来源、缺失渠道和脱敏行为。合成渠道不作外呼，
不等于SMTP/飞书实际可连或终端实收；只有真实批准试验才能补齐这些证据。

2026-10-27实际运行：本工具、原notification_service与B派发worker受影响回归
共38项通过、零跳过；真实渠道对象+PG的CLI组合预检也在其中，send调用数0，
attempt仍为0。Ruff/格式检查通过，mypy完整app的117文件零问题。
专属容器/网络退出清理通过。该结果不代完整E1或真实通知实收。
