# 跨模块公共约定草案 v0.1

## 2026-10-19 已发布入口（优先于全部历史）

PR23经C同头审查与适用检查通过，已正常合入共同main
`46fa463d6ca4f71965b8db74b08a41f352b05cfb`。三方接受的固定包为
`f98e65b4717334bae34f7495181c320ae5061860`，正文与manifest字节不变，见
[S0-SIGNOFF](S0-SIGNOFF.md)及[实际签收/发布记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5)。
固定包中的PROPOSED是生成日快照，不改包字节来改写过去；当前规范目标按其
F01—F06及恢复决议执行，未实现业务不伪称完成。S0仍待B/C取得/本人接入记录，
完整E1/两通知/14设备不是额外S0前置。唯一已发布迁移源码head仍为a03。

### 2026-10-17 统一包生成时的入口（历史）

当时请签收S0-SIGNOFF与固定manifest的同一完整提交，逐项确认六接口和恢复；
当前签收/发布状态以上段和#5为准。截止和阶段目标见
[DELIVERY-CHECKPOINTS](../docs/team/DELIVERY-CHECKPOINTS.md)。

## 2026-10-14 RC2 接续说明（历史，保留权限与前缀约束）

共同 main 已核实为 `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813`；B 为
`58546e9993381b92d158cb437a31d61b95dfb1c1`，C 为
`6f38c1cda4442ebe868720791fff0616731f2397`。旧日期段落仅作历史。

- F01 的身份封装、原始值保留和遥测 `schema_version=1.0` 沿已确认提案执行。
- F06 的固定查询快照首版采用 900 秒 TTL、10000 条上限、每 actor 4 个、
  全局 64 个；容量不足 429 `snapshot_capacity` 与 `Retry-After: 30`。
  A 接受 B/C 的容量建议；这不是 C 的持久快照已实现或性能实测证明。
- F02 的开放版本链接方向接受，但三版 S1→S2→S3 写入已实际复现失败，
  B 修复并补回归后才能签收，不能以两版测试替代。
- F03 输入及追溯字段尚未三方一致。A 推荐[RC2 单批候选提案](F03-RC2.md)，
  要求 B/C 对同一提交答复；不把现有 B 最小 DTO 或 C 提案当作接线成功。
- F04 的恢复事件使用新 ID 并关联原事件；未绑定设备、发生时间字段与
  真实设备身份校验仍须落实到服务边界及案例。

根路由只挂载模块 router，不再添加 `/api/v1`；外层 `app.main` 统一加前缀。
外部 HTTP 候选/设备事件写入首版仅管理员，活跃普通用户保留查询；内部
A/C 管道沿可信封装调用服务，不能用放开普通用户写来代替服务身份设计。

S0 关闭条件仍是三方确认同一契约提交及样例、工作区包含审核基线、接线
与权限复测通过；本更新不关闭 S0，也不代表 E1 或两种通知实收。

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
