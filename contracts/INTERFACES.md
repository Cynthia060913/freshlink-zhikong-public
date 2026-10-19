# 跨模块公共约定草案 v0.1

## 2026-10-17 统一签收入口（优先于下面历史）

请签收 [S0-SIGNOFF.md](S0-SIGNOFF.md) 与其固定版本 manifest 的同一完整提交，
逐项确认 F01—F06 及数据异常恢复三个决议。当前仍 PROPOSED，不能引用下面
旧日期 SHA 宣称已冻结。截止和阶段目标见共同 main 的
[DELIVERY-CHECKPOINTS](../docs/team/DELIVERY-CHECKPOINTS.md)。

2026-10-12最新：C已在#5的6126476107接受F01/F06第7节路径/page/认证与CSV目标，并指出时间水位的晚提交并发漏洞。A第8节提出固定snapshot_id成员/版本与15分钟有效期；CSV新增必填snapshot_id，仍待C/B接受。旧“仅同水位就集合一致”不再作为充分方案。心跳新增纯转换plan，不等于主程序持久在线状态。

2026-10-12收尾入口：[S0最小收尾包](S0-CLOSEOUT.md)。它汇总已满足项、六类接口的具体剩余确认、签收格式，以及可运行A侧样例测试。RC1仍为待签收提案，S0不要求B/C完整业务或E1先完成；正式冻结需三方明确确认并进入共同版本。

2026-10-12晚更新：C在#5的6125196948明确接受F01、提出F02/F03具体待定项与F06签名；A的[第7节补答](../docs/modules/A/01-contract-proposal.md)及[HTTP候选样例](examples/a-query-integration-rc1.json)给出路径/当前认证/签名配置。候选CSV前缀统一为单引号人工查看目标，原`[text] `只是历史替代。B/C剩余确认与共同合入尚未完成，文档仍为proposal。

2026-10-11：A 已结合 B/C 提案提交 [A 侧答复及 RC1 建议](../docs/modules/A/01-contract-proposal.md)。该提案尚未三方确认，没有将本草案升级为正式接口。RC1 区分契约版本与遥测 schema_version；查询/CSV、候选结果和规则版本的待确认项必须显式答复。实际共同提交号及确认记录在 S0 #5。

状态：用于三位 Agent 对齐，不表示对应接口已实现。本机用户负责 A，另外两位负责 B/C，正式业务表和 OpenAPI 在首轮实施时由模块作者共同补齐。新增字段需兼容，改变含义需更新版本与交接。

统一：UUID 标识；UTC ISO 8601 带时区；页面北京时间；温度 °C、相对湿度 %RH、功率 W、电量 kWh。保留采样时刻与服务接收时刻，模拟加速单独记录。数据足够/缺失/无效不能与低风险混淆。

| 合作点 | 主责与消费 | 最低字段/行为 |
|---|---|---|
| 合法遥测 | A→C | schema_version、device_id、warehouse_id、message_id、sampled_at、received_at、metrics（key/value/unit）；A 完成设备身份判断，C 保留原始观测和处理质量 |
| 设备事件 | A→B | source_event_id、device_id、warehouse_id、occurred_at、kind（offline/recovered/fault）、reason；B 幂等生成或恢复事件 |
| 数据风险候选 | C→B | source_event_id、observation_ids、warehouse_id、batch_ids、occurred_at、kind、severity、evidence、algorithm_version；不包含伪造的发送结果 |
| 批次与规则快照 | B→C | batch_id、入库/出库时刻、warehouse_id、品种、rule_version、规则范围、来源；不可偷偷变化历史规则 |
| 遥测/风险查询 | C→A/B | 对象、时间范围、游标、单位、质量、分数/数据不足状态；不能用空数据替代低风险 |
| 控制与记录 | A→B/网页 | command_id、device_id、action、参数、issued_at、expires_at、状态、回执/失败原因、可选 event_id |

MQTT 主题草案：`freshlink/v1/devices/{device_id}/telemetry`、`heartbeat`、`commands`、`acks`。接入鉴权后才可用于集成；P0 未启动 MQTT。

命令状态：queued→sent→acknowledged / failed / timeout。Broker 收到不等于设备执行。告警状态：open→acknowledged→resolved→closed；人工确认不等于故障已恢复。

异常候选需持久化可重试，按 source_event_id 去重。数据与风险事件关联保留，不能因一次模块调用失败静默丢弃；使用现有数据库即可，初版不新增分布式消息架构。

建议模块路径：`backend/app/modules/iot`（A）、`warehouse`/`alerts`（B）、`telemetry`/`analytics`（C）；对应前端 `frontend/src/features/` 下独立目录。保留模板既有登录和路由体系，不强行重构。公共入口由轮值集成人处理。
