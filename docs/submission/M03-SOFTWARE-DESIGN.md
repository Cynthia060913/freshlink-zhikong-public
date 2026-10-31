# 鲜链智控软件设计文档（M03 汇编稿）

**汇编**：B（xixidamei），2026-10-30 修订。
**汇编基准（双轨如实）**：
- **当前发布点**：共同 main `1a2fa53be38a83a42df65395adf88332d5cdf3ed`
  （PR #99 合入点），与受测应用源
  `4b9bd50484503d8b02d5f87405490c7b13aae5e9` 全树一致；含 A 入口/品牌
  `7382ff8` 与 C 兼容冻结逐批规则证据 + 显式 CSV v2（`4b9bd50`，精确 C
  业务源 `ab2a7b6`，C 完整 25 文件正式批准）。**PR #99 已发布，不再写
  "尚未发布"**。本稿的通知派发语义（§5.5/§5.6）与多批次投影行为（§5.1）
  按该源描述。
- **E1 实测证据源（历史事实，限定签收，不重跑不改标）**：一库同版同事件
  E1 与双渠道终端实收在 `e7fffd61`（= main `319aa63` 全树一致）取得；
  其后合入的 B 通知安全修复（PR #96 全链 `5fa963c…f8c0b00`，经 PR #98
  组合头 `4e7491f` 进 main）与 PR #99 均为**单列增量**，旧实收证据不因
  新头改写。
- 关联更正契约 PR #80/#83 与 A PR #94 已在两轨源中均合入。
**章节来源**：需求/架构/ER/数据字典来自
[A-SYSTEM-DESIGN.md](A-SYSTEM-DESIGN.md) 与
[design/README.md](design/README.md)（A，基于业务源码 `71e5a882`，只读反射
33 表核对）；测试章节来自 [A-TEST-REPORT-CANDIDATE.md](A-TEST-REPORT-CANDIDATE.md)
（A）、`docs/modules/C/07-team-test-report-draft.md`（C）与 B 模块第一手测试；
B 模块章节（仓储/批次/规则/告警生命周期/更正/通知派发）由 B 按当前源码撰写。
**状态**：M03 未签收；本文是可阅读的完整汇编稿，各成员对自己章节复核后
逐项实签。所有"未验证/待验收"标记保留，实际通过后再更新。

---

## 1. 需求分析

系统面向两座苹果冷库、三批同品种苹果，软件模拟七个测区，每区一个温度采集
身份和一个制冷执行身份，共十四个逻辑设备。不采购硬件或云服务器；模拟数据
不是实际果品品质实验，阈值异常不宣称为真实腐败概率。智能算法为加分项，
本轮以版本化阈值规则为基线，未启用模型算法（详见 §10 限制）。

必选业务（对应竞赛通知要求）：

1. **设备注册与绑定、在线状态**：注册设备身份、绑定冷库、心跳停发判离线、
   重连恢复（A 主责，B 提供异常追溯关联）。
2. **实时采集、清洗、历史与导出**：MQTT 持续采样 → raw/质量 → 同筛选历史
   查询与 CSV 导出（A+C 主责）。
3. **至少两种告警且可追溯**：温度高/低（另含数据质量、设备离线/故障等
   kind），告警可人工处置（ACK）、机器判定恢复、晚到坏证据更正；通知经
   **SMTP 与飞书手机端两个独立渠道分别终端实收**，失败重试、来源 ID 稳定
   可追溯。服务接受 ≠ 终端实收。
4. **人工控制与执行闭环**：登录用户下发制冷命令 → 持久命令/尝试 → MQTT
   执行 → 设备执行 ACK 与回执 → 后续温度反映效果（A 主责）。
5. **权限**：普通用户只读、越权拒绝、重复操作幂等。

## 2. 系统架构

图：[当前架构图（e7）](claude-b-materials/assets/CURRENT-ARCHITECTURE-e7.png)（A
集成方按精确 e7 来源——与共同 main `319aa63` 全树一致——的原架构图更新：
保留组件、连线与唯一迁移链 A03→B01→A04→A05→C01，通知框由旧"尚未验收"改为
"单事件双渠道已实收"；虚线仍为外部渠道边界，不表示每事件自动获准发送。
图件来源 PR #89 头 `b4d8c28`，SHA256 `81a7cbf11cd689891663211ed83991c746ffbdefe32f7befc1c0488a9e4778f8`，可编辑源与单页 PDF 同目录见 A 材料
`claude-final-materials/`；旧版
[架构图](design/tikz_architecture.png)（可编辑源
[tikz_architecture.tex](design/tikz_architecture.tex)，通知框标"待验收"）
保留为历史版本，未重标）。

- **展示层**：React/TypeScript/Vite；公共认证与导航；设备、控制、B 告警、
  C 数据页面。
- **HTTP 层**：FastAPI。`backend/app/main.py` 仅在总入口加一次
  `settings.API_V1_STR`；`backend/app/api/main.py` 聚合子路由（设备、绑定、
  控制、B 告警——告警路由挂 `guard_alert_ingestion` 摄取门禁），不在
  alerts 聚合器重复加前缀。
- **感知/执行层**：`simulator_runtime.py` 设备运行器、`simulator_fleet.py`
  七测区监督器；独立设备身份、MQTT ACL、逐测区 SQLite 本地执行状态
  （SQLite 仅模拟器本地状态，不是服务器业务库）。
- **A 服务层**（`backend/app/modules/iot/`）：注册/心跳/在线离线、控制事务
  与回执、MQTT 接纳与持久收件队列、有界租约与重试、A→B 设备事件投递、
  通知运行入口 `notification_service.py`（§7）。
- **B 服务层**（`backend/app/modules/alerts/`）：冷库/批次、版本化规则快照、
  告警事件、状态迁移、通知派发与尝试审计（§5）。
- **C 服务层**（`backend/app/modules/telemetry/`）：观测 raw/质量、历史、
  固定快照分页/CSV、评价、outbox 与恢复进度（候选端口，C 主责收尾）。
- **存储层**：PostgreSQL 33 张业务/模板表；Mosquitto 设备消息路由。

## 3. 核心业务链与验收口径

数据/告警链：

```
设备温度消息 → A 校验身份/绑定与持久接纳 → C 同事务保存 raw/质量/观测
→ 历史页面与同快照 CSV → A 调用方选择 B 生效历史规则 → C 门禁候选与持久 outbox
→ B 摄取接口接受（来源摘要回读/追溯）→ 已提交事件通知派发
→ SMTP 与飞书终端分别实收
```

控制链：登录用户下发 → 持久命令/尝试 → MQTT 执行设备 → 设备事务保存执行
结果并返回原 ACK → 服务端回执 → 后续采样反映制冷效果。仅创建命令、publish
成功或页面显示"发送"不算已执行。

恢复/更正要求：完整可信恢复窗口、关联原异常、重放/并发安全；恢复关闭异常
不删除原始超温与历史暴露；晚到坏证据以更正事件追溯，不改写历史（§5.4）。

## 4. 数据库设计

- **图件**：[设备控制ER图](design/tikz_er_a.png)、
  [仓储告警ER图](design/tikz_er_b.png)、[观测快照ER图](design/tikz_er_c.png)
  （可编辑 `.tex` 源同目录；ER 箭头只表示真实数据库 FK，从被引用父表指向
  引用子表）。
- **范围**：唯一迁移链 A03→B01→A04→A05→C01（C01 唯一 head），专属空库
  实际升级 + model check 后只读反射 33 张表；字段/PK/FK/唯一约束/索引
  全量见 [DATA-DICTIONARY.md](design/DATA-DICTIONARY.md) 与
  `design/schema-inventory.json`（反射输出 SHA256
  `d43cc6519a7c…c432bd5`，见 design/README）。
- **B 核心关系**（真实 FK，源码 `backend/app/modules/alerts/models/`）：

| 父 | 子 | 关系 |
|---|---|---|
| alerts_warehouse | alerts_batch | warehouse_id FK |
| alerts_batch | alerts_batch_rule_snapshot | batch_id FK（版本化规则快照） |
| alerts_batch / alerts_warehouse | alerts_event | 可选 FK |
| alerts_event | alerts_status_transition | alert_event_id FK（append-only 生命周期） |
| alerts_event | alerts_notification_attempt | alert_event_id FK（append-only 尝试审计） |

- 未绘入三张 ER 图的表（收件队列、前沿、冲突审计、恢复进度等）不是不存在；
  `iot_device.warehouse_id` 等业务 UUID 引用不是 DB 外键，不擅自补线。

## 5. B 模块设计（B 撰写，源码 `backend/app/modules/alerts/`）

### 5.1 仓储、批次与版本化规则

`warehouses.py`/`batches.py`：唯一编码约束，重复注册译为领域错误。
`rule_snapshots.py`/`models/rule.py`：规则以**批次级快照**持久化
（`alerts_batch_rule_snapshot`），异常发生时引用当时的 `rule_snapshot_id` +
`rule_version`——**用历史规则评价历史数据，不用今天的新规则覆盖过去**；
恢复事件必须保留原异常的规则身份（§5.3）。多批次按已签 F02 规则选择生效
历史快照，不任选第一批。
**多批次结果的规则版本归属（B 业务口径；据 C 只读诊断 #5 6489558890 /
文档源 `0ec45edf`、C 事实确认 #12 6520531405）**：逐批规则版本持久保存在
**观测**的 `risk.batches[*].rule_version`/`rule_snapshot_id`（C 实测 540 个
批次结果全部非空），不是泛称"事件 risk"。旧 f6 发布源 `f6f1194` 已存
逐批规则但历史/默认 14 列未显示——属**当时展示缺口，不是"无规则判
normal"**（旧空列按有日期历史保留，不追改）。**PR #99 `4b9bd50` 已修**
（发布点 `1a2fa53`）：单批标量 `rule_version` 原样保留；逐批冻结引用另列
`batch_rule_refs`（由 `risk.batches[*]` 投影，不解析今天的 B 规则）；默认
CSV 仍为 14 列（v1 逐单元格不变），显式 `export_schema=2` 提供 15 列
（含 `batch_rule_refs_json`）；旧成员无证据保持 `null`，不补今天的规则。
已发生 normal 判定与逐批规则证据分开表述，不以空列冒充无生效规则。

### 5.2 告警事件摄取与五态回执

`services/events.py` `receive_candidate`：统一五态回执
`CandidateReceipt`（`accepted_new` / `accepted_duplicate` / `conflict` /
`invalid` / `retryable_failure`），幂等键为全局唯一 `source_event_id`，
正文摘要 `compute_content_digest`：同 source 同摘要幂等重放、同 source
改正文 `conflict`/`content_digest_mismatch`。事件行**不可变**；现状由
append-only 的 `alerts_status_transition` 推导。并发重放经唯一约束兜底：
integrity 失败回读已存行再分类（`_insert_event`）。瞬时存储失败映射
`retryable_failure`（`retry_after_s`），不泄露载荷。

### 5.3 告警生命周期：ACK 与恢复的行锁串行化

- **状态机**：`open → acknowledged`（人工 ACK）、`open/acknowledged →
  resolved`（机器判定恢复）；append-only，无回退路径。
- **ACK**（`acknowledge_alert`）：先 `_lock_alert_event` 对原异常行
  `SELECT … FOR UPDATE`，锁后重读状态；重复 ACK 幂等（不写第二条迁移），
  已 resolved 拒绝（`AlertAlreadyResolved`），拒绝路径先 rollback 释放锁。
- **恢复**（`_recovery_transition`）：取**同一把**原异常行锁
  （`_lock_alert_by_source`），锁后重读；原异常必须存在且为温度类异常，
  恢复事件的 `rule_snapshot_id`/`rule_version`/`severity` 必须与原异常
  一致（不匹配在写前拒绝）；迁移行与恢复事件行同事务原子提交；原异常已
  resolved 时恢复落地但不写冗余迁移。数据库失败完整回滚 → retryable。
- **并发证明**：真实 PG 双连接回归锁定原异常行后交错 ACK/恢复，双确认只
  留一条迁移、恢复不回退已确认（main 上回归位于
  `tests/modules/alerts/test_recovery_candidates.py` 等；SQLite 不作锁证据）。

### 5.4 关联数据质量更正（F03 契约，PR #80/#83 已合入冻结基线）

晚到坏证据以 `kind=data_quality` 候选 + `related_source_event_id`（指向
原异常 `source_event_id`）表达（#5 6394581672）：

- DTO 校验（`models/event.py`）：`related_source_event_id` 仅允许
  `temperature_recovered`（必填）与 `data_quality`（可选），其余 kind 422。
- `_correction_related_check`：**与 ACK/恢复共用同一把原异常行锁**、锁后
  重读；原事件必须存在且同库同批，否则 `unknown_related_event` /
  `related_event_mismatch` 永久 invalid、零部分写入；语义拒绝先 rollback
  释放锁；瞬时读失败 retryable。
- **更正永不写状态迁移行**：resolved 不回退、open 不重开、历史不改写；
  更正行本身（含双 ID 关联）即追溯记录，后续恢复及其重放不能清除更正提示。
- 幂等沿用通用分类：同 source 同摘要 `accepted_duplicate`、改正文
  `conflict`。真实 PG 回归 21 条
  （`tests/modules/alerts/test_data_quality_correction.py`，PR #80 组合头
  `d1f5359` 定稿、已随 main `319aa63` 合入），含双连接锁
  交错：更正在并发提交的恢复之后执行不回退 resolved、迁移行仍只一条。

### 5.5 通知派发层（`notifications/dispatch.py`）

- **Envelope**：每事件×每渠道构建消息；正文含来源事件 ID、关联事件 ID
  （双 ID，§5.4）、采样时间（Asia/Shanghai）、内部告警记录 UUID。
- **渠道选择**（`build_transports`）：只收集**已配置**渠道（SMTP 由
  settings、飞书由环境变量 webhook）；未配置静默跳过；单渠道配置损坏不拖垮
  另一渠道。
- **有界重试**：仅对 transient/timeout 重试，指数退避与 Retry-After 取大、
  封顶；permanent 不重试；**接受未证实或意图完成写未证实的结果绝不进入
  重试**（见下）。
- **发送前持久意图（main `1a2fa53` 现行为，修复经 PR #96→#98）**：每次网络外发
  **之前**，先在独立事务持久化一行 `send_intent` attempt 行；**该行写失败
  则本次零发送**（provider 调用为 0，预算内只重试守卫写入、不触网）。
- **发送后单向完成与保守暂停**：发送返回后按**精确意图行 ID + 未完成状态
  条件**把该行单向完成为最终结果（已完成行绝不改写）；完成写失败或无法
  确认完成时，**同一次派发立即停止**——无论 transport 结果看起来多可重试，
  本轮不再对 provider 发起任何尝试（A review 5686551361 复现形状的修复）。
  完成写失败、结果不明或进程中断都会留下未完成的 `send_intent` 行——
  去重守卫与 pending 扫描把该渠道保持为**待人工核验**，后续运行与重启
  **保守暂停、不自动补发**。
- **接受未证实（`acceptance_unknown`）**：DATA 返回前的连接类中断打上
  结构化标志：结果如实 `delivered=False`、同次派发**不重试**，attempt 行
  以固定哨兵值落库（不写成可重试的"未送达"），跨派发与重启都待人工核验
  （四值 `DeliveryOutcome` 契约不变）。
- **尝试审计**：每次尝试（含失败）追加 `alerts_notification_attempt` 行；
  独立 attempt session 提交（或在 SAVEPOINT 内），**审计失败绝不 rollback
  业务事务**，只在结果 detail 如实标注。
- **同源去重**：同一 (事件, 渠道) 已有 delivered 记录、或已有
  `acceptance_unknown` 或未完成 `send_intent` 记录则跳过；去重查询自身
  失败时**保守 fail-closed 零发送**（`dedup_error=True`）。
- **渠道隔离**：单渠道违反契约只影响该渠道，异常文本/URL 不入库（固定
  错误码）。
- **只消费已提交事件**：`dispatch_committed_event` 在业务事务提交后由
  worker 调用；网络与退避 sleep 不进业务事务、行锁或 HTTP 请求内。
- **SMTP 固定阶段诊断**（同上源）：适配器以封闭枚举 `SMTPStage`
  （connect/tls/auth/data/post_send_cleanup）记录停止阶段；`DATA` 被服务器
  接受后（`send_message` 返回）的**任何收尾异常（QUIT/SSL EOF 等）按
  accepted 上报**并如实标注"terminal receipt unconfirmed; do not
  auto-retry"；收尾异常时已取得的部分拒收结果**原样保留**在
  `recipient_outcomes`（原 4xx/5xx 分类，已接受者不重发，审计不写成
  "全部接受"）。**如实边界**：数据库无逐收件人结构列，未被接受的对象
  （如 450）暂无自动补发路径；**正式发行入口按 PR #97（已合 main
  `9978f0f`）限定单个已授权邮箱**——低层适配器可解析多地址属于协议层
  能力，**不写成正式实发支持多收件人**；多收件人为明确排除的非发行
  场景。失败 detail 只含固定前缀、阶段与数字回复码，异常文本/完整
  响应/地址/凭据一律不入库。

### 5.6 持久派发 runner（`notifications/runner.py`）

- **Pending 规则**（main `1a2fa53` 现行为，修复经 PR #96→#98）：事件 pending ⇔
  至少一个配置渠道既缺 delivered 尝试行，也缺 `acceptance_unknown` 或
  未完成 `send_intent` 尝试行（后两者=该渠道已被结算为**待人工核验**，
  不再视为欠发），总尝试数 <
  `max_attempts_per_event`（12），最新尝试早于 `min_attempt_age_s`
  （60s，重启不清零的持久退避——预算在表里，不在内存）；oldest-first，
  部分送达的只补缺失渠道，不重复打扰。旧基线 `319aa63` 的 pending 规则
  只按 delivered 判定，为有日期历史。
- **单派发互斥**：`_DispatchLease` 在**专用连接**上持 PG session 级
  advisory lock（不进业务事务、不占告警行锁）；第二实例拿到锁即返回
  `interrupted="lease_busy"` 零发送；连接死亡 PG 自动放锁；每 cycle ping，
  失败即停止认领新派发。
- **Dry-run**：只报告 pending，零写零发。
- **失败诚实记录**：cycle 异常记异常类型名并停止认领；默认无渠道配置时
  惰性 no-op。真实 PG 互斥/持久预算/重启续跑回归见
  `tests/modules/alerts/test_dispatch_runner.py`。

## 6. A / C 模块设计（摘要，全文见来源）

- **A**：设备注册/心跳/在线离线判定、控制命令事务与执行回执、MQTT 接纳
  与持久队列、A→B 事件投递；运行入口与边界详见
  [A-SYSTEM-DESIGN.md](A-SYSTEM-DESIGN.md) §2/§5。
- **C**：观测接纳与同事务 raw/质量写入、历史快照分页/CSV、恢复 outbox 与
  冻结前沿门禁（`begin_evidence_write` 同调用方 Connection 门禁 + 冻结
  stamp）；端口收尾状态见 `docs/modules/C/07-team-test-report-draft.md`。

## 7. 通知运行入口与派发接点

实际派发入口为 `backend/app/modules/iot/notification_service.py`
（A 撰写，包裹 B 的 `run_dispatch_worker`）：

- 显式操作员命令（`--config` 私有配置 + `--send` 开关）；**默认 dry-run**；
  无导入期 worker、迁移、provider 请求或环境修改——应用启动/迁移不会被
  变成自动发送。
- 私有配置提供收件人（≤20 个 EmailStr），不进源码/命令行/日志。
- 仅 PostgreSQL（dialect 校验）；独立 owned engine（连接/语句超时、
  pool_pre_ping）；SIGINT/SIGTERM 优雅停止。
- 输出为脱敏 JSON（渠道、pending 计数、interrupted、
  `terminal_receipt_confirmed=False`——服务接受不写成终端实收）。

## 8. 安全、配置与部署

- **认证与权限**：公共登录权限（login/users 路由）；设备绑定、控制、遥测
  复用登录权限；B 摄取路由挂 `guard_alert_ingestion` 门禁；登录权限、摄取
  权限、设备身份、通知收件授权分别验证，不互相替代。
- **设备面**：逐设备账户/MQTT ACL；真实部署默认 TLS，隔离证明的明文
  broker 只在随机内部网络。
- **秘密**：真实配置/邮箱/授权码/机器人 webhook/原数据库不进源码、提交、
  日志或材料包；派发层异常文本不入库。
- **迁移**：唯一链 A03→B01→A04→A05→C01；未升级任何原数据库；显式空库
  起点函数不从启动过程调用。

## 9. 测试报告（汇编）

每条记录：版本、环境、步骤、实际、证据、未测。Mock / 服务接受 / 终端实收 /
跨模块实链分开陈述。完整逐条见
[A-TEST-REPORT-CANDIDATE.md](A-TEST-REPORT-CANDIDATE.md)（A）、
`docs/modules/C/07-team-test-report-draft.md`（C）；以下为 B 模块第一手与
全量回归摘要：

| # | 范围 | 环境 | 结果 | 证据位置 |
|---|---|---|---|---|
| 1 | B 告警全量套件（含更正 21 条、runner、派发、恢复、迁移、SMTP 阶段诊断负向、接受未证实与发送前持久意图/同轮停止组合回归） | 真实 PG 18 / Python 3.14，本机隔离实例 | 419 passed, 2 skipped（PG opt-in）, 17 subtests | `backend/tests/modules/alerts/`，PR #96 分支头 `f8c0b00` 工作树实跑（2026-10-29）；**分支级成绩，按源分层列示，不与其它层相加冒充全系统成绩**。该头已随 PR #98 入 main（组合头 `4e7491f`），主线成绩以对应 CI 为准 |
| 2 | 更正契约回归：首收/重放/冲突/未知原异常/跨批/更正先于恢复/旧恢复重放/DTO 拒绝 | 真实 PG | 21 passed | `tests/modules/alerts/test_data_quality_correction.py`（PR #80 组合头 `d1f5359`，已入 main） |
| 3 | 双连接锁交错：更正排队于原异常行锁后，不回退并发提交的 resolved | 真实 PG 双连接 | passed | 同上（`test_correction_serializes_behind_held_original_row_lock`） |
| 4 | ACK/恢复行锁串行化：双确认只留一条、恢复不回退 | 真实 PG 双连接 | passed | `tests/modules/alerts/test_recovery_candidates.py`（A 审入 ae2a941） |
| 5 | runner 互斥/持久预算/dry-run/重启续跑；接受未证实渠道跨重启不自动补发 | 真实 PG | passed（互斥为双实例真实争锁） | `tests/modules/alerts/test_dispatch_runner.py` |
| 6 | 全量后端回归（CI） | GitHub Actions Python 3.14 / 隔离 PG | PR #80 组合头 `d1f5359` CI 9 workflows 全绿；PR #96 头 `f8c0b00` 七项适用 workflow 全 success（pre-commit / Test Backend / Playwright / Test Docker Compose / Zizmor / Test archive / Conflict detector）；PR #98（main `1038983b`）与 PR #99（发布点 `1a2fa53`）以各自 PR checks 为准 | PR #96 / PR #98 / PR #99 checks |
| 7 | A 组合链：14 设备/7 流/执行 ACK 后降温、离线重启恢复、CSV 对拍 | 精确 `62bb2ef` 受控环境 | 见 A46 记录 | [A-TEST-REPORT-CANDIDATE.md](A-TEST-REPORT-CANDIDATE.md) |
| 8 | **一库同版同事件 E1 实测出口通过**（#6 6459911129，2026-10-28） | 独立试验库；来源 `e7fffd61`（与 main `319aa63` 全树一致） | 真实认证 MQTT→A 接纳→C raw/质量→B 真实 TCP 异常；授权后 SMTP 与飞书各一次，**两服务接受且队长本人分别确认终端实收**（"邮件和手机飞书均收到，来源 ID 一致"，来源 `d89c0bf9`）；控制 `0c8275df` 执行一次、恢复 `1aadba77` 关联正确；真实浏览器实下载 281 行 14 列 CSV 与 15 页全部观测 ID 一致；普通账号控制/告警确认 POST 均 403；只读 SQL 复核仅两条通知 attempt（两渠道均 delivered） | #6 6459911129；`E1-ACCEPTANCE.json` SHA256 `13500940875c0fd8aed244ad9d2a75907a75910a2d3cd39a5474428a36856470` |
| 9 | C 第二机（PR #94 精确 e7 独立构建，#6 6449022153，2026-10-28） | C 第二台电脑，干净检出 `e7fffd61` | 独立源码归档/构建镜像（源码 ZIP `b90a7b33…`）；默认 CMD 无源码挂载真实 HTTP 检查通过（健康/匿名 401/登录/注册 201·409/详情）；14 设备夹具（2 冷库/3 批/7 采集流/7 执行 ACK/14 离线恢复追溯）；普通用户真实页面（设备 14 台/2 冷库、遥测快照 10 条、告警 3 条只读）；同用户 HTTP 实取快照 CSV 200、10 行 14 列（`23CA765A…`）；普通用户 POST 403、匿名 CSV 401。**API 保存的文件不写成浏览器下载** | #6 6449022153 |
| 10 | A 离线交互制品入口（PR #95 精确 `cdb7d95`） | A 本机 | 14 在线、页面控制执行 ACK、持续新采集、同快照 CSV 15 行全 ID 相同；三分钟窗口四子进程退出 0；无网络 PG 重启回读 1202 条观测保持 | #8（A 交接 `b4d8c28`）/ PR #95 |
| 9b | C 第二机浏览器实测（离线交互入口包，PR #11 精确 `bfc55da`，2026-10-29） | C 第二台电脑；外层包提交 `03b6002` / 应用镜像 `e7fffd61`（≠ main `9978f0f` 新镜像，不混标） | 包完整性：ZIP 719157924 字节（SHA256 `632ee61c…`）、manifest `609b072f…` 校验通过后才启动；管理员真实登录、14 台在线；一次制冷命令 `6047b9e1` 排队→MQTT 接受→设备 executed ACK，随后同设备新增已存观测（时间顺序不单独断言控制因果）；同设备固定快照 257 条、遍历 13 页 257 行，**浏览器按钮实际下载** `observations.csv` 63387 字节、14 列、257 行、257 个互异观测 ID（SHA256 `1f27794f…`）与全部分页逐行同序一致；匿名历史接口 401；五分钟四子进程退出码均 0，本轮容器/网络清理为 0 | [#9 6475341383](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6475341383) / #12 6476180902；C 文档 PR #11 `bfc55da`（B 已独立复审） |
| 11 | 历史记录（有日期，非当前出口）：10-27 A 通知实测一次 SMTP SSL EOF（当时实收待核、未补发，后被行 8 同版 E1 双渠道实收取代）；旧 C 第二机 `0db9402` GuardedRecovery 降温检查两次失败 | — | 均按原值保留、不篡改、不拼接 | #8 6440797666 / PR #11（C 测试材料） |
| 12 | A：PR #99 集成与 4b9 运行验证（#5 6503785874，2026-10-29，**A 本人运行**） | A 本机；PR #99 精确 `4b9bd50`（A 入口 14 文件 + C 业务源 `ab2a7b6` 11 文件，共 25 文件，C 正式批准） | 完整后端 **1492 passed / 0 skipped / 91%**；正式迁移无差异；独立 PG 冻结/旧成员/授权 CSV 专项通过；归档镜像真实浏览器 14 在线、实际控制 ACK、新增采集；**150 行 15 列 / 8 页**逐行同快照、全部 ID 与字段语义对应；默认 v1 14 列逐单元格不变；149 行两批引用 + 1 行缺证据 `null`（不补今天规则）；匿名 401、普通控制 403、非法 schema 422；3 分钟 4 角色退出 0 | #5 6503785874；PR #99 checks |
| 13 | A：整理包实际外部入口复验（#9 6504945460，2026-10-29，**A 本人运行**） | A 本机；4b9 整理后包 + manifest `93ebde00…` 校验通过后启动 | **100 行 15 列 / 5 页**全字段同快照对拍；14 在线、实际控制、401/403、3 分钟 4 角色退出 0 及受限停机。**与行 12 的 150 行为两个独立快照，不拼接** | #9 6504945460 |
| 14 | C：旧 f6 第二机两次运行（**分列，不拼同一文件**；#9 6489450099 / 6502694128） | C 第二台电脑；f6 发布源 `f6f1194` | ①浏览器下载 264 行/14 列（SHA256 `eaef5536…`）；②另一次随机项目同快照 **HTTP** CSV 265 行/14 列与 14 页 265 个 ID 同序，并实测匿名历史 401、普通用户控制 POST 403、五分钟四角色退出 0。两份属**不同快照**；不写成 4b9 结果 | #9 6489450099 / 6502694128（C 本人记录） |
| 15 | A：f6 同源三批分段（#8 6488688195 第 6 点；A#7 6487698969，**ASGI 分段**） | A 受控环境；f6 同源 | 2 库 3 批 14 设备、7 流/7 执行 ACK/14 离线恢复；**3 条不同批次高温经真实 MQTT 到已认证 B ASGI HTTP 落库并读回历史规则**，发送器重启 0 重发；f6 页面 106 行 14 列 CSV 与 6 页全 14 字段/顺序相同；匿名 401/普通控制 403、五分钟退出通过。**ASGI 分段，不写成 TCP 或三批各自完整 E1** | #8 6488688195 / A#7 6487698969 |
| 16 | C：4b9 新包有限第二机（**C 本人实测**，2026-10-31） | C 第二台电脑；来源 `4b9bd50`（与发布点 `1a2fa53` 全树一致）；运行 ZIP 715242775 字节（SHA256 `1af60e09…`）核验通过后启动 | 新随机专属项目 5 分钟四角色退出 0；管理员真实 Chrome/Playwright 登录、14/14 在线；一次控制 executed ACK 后同设备新增已存观测；同一固定快照 7 页/123 互异 ID 与**浏览器实际下载** 15 列/123 行 CSV 逐行全字段一致；逐批规则展开；匿名历史/CSV 401、普通账号控制 POST 403；受限停机后本轮容器/网络清理 0、DB 证据卷保留。**另一次快照默认 14 列与显式 15 列各 269 行（前 14 列逐格一致）属不同快照，不与 123 行文件拼接**；C 两份章节源 PR #102 精确 `a5ceadb6`（新头 CI 重跑中，不沿用旧头结果） | [#9 6527639497](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6527639497) / #12 6527749634 / 6528156867 |

**如实未测**（不写成通过）：第二机自然持续恢复（14 设备夹具段未跑 180s
sustained，`notifications_sent=0`；10-29 离线交互入口包运行同样未测 180s
持续恢复）；C 浏览器下载核验已由行 9b（2026-10-29 交互入口包，257 行
逐行对拍）完成——10-28 PR94 e7 段"下载路径未取得"属该段历史事实，不
追溯改写；该交互包普通用户写入 403 未测；最终第二机总签收与 M01—M05
总签收；实际投稿；runner 组合进 A 运行包的现场验证；SIGTERM 派发中途
窗口；真实网络下发送前意图/完成写时序（本机仅合成 provider）；
C 的 f6 第二机段（普通用户 403、匿名 401、全 14 页 ID 逐项比对）未测
项由 C 本人范围如实记录（#5 6489558890），A 测试不代称 C 测试；
多批次逐批规则引用已由 PR #99 `4b9bd50` 修复入发布点 `1a2fa53`
（§5.1）；旧 f6 包默认列空为有日期历史；新 4b9 运行包的有限第二机
（逐批展开/15 列真实浏览器 CSV/401·403/正常停机）已由 C 本人
2026-10-31 实测（行 16，#9 6527639497），其**自然持续恢复仍未测**，
B 不代签。

## 10. 限制与未验证清单

1. 双渠道通知终端实收：**已由队长本人在一库同版同事件 E1 中确认**（#6
   6459911129，2026-10-28："邮件和手机飞书均收到，来源 ID 一致"；运行来源
   `e7fffd61` 与 main `319aa63` 全树一致）。服务接受与真人确认分别保存，
   服务接受 ≠ 终端实收的记录纪律不变；10-27 旧 SMTP SSL EOF 为有日期
   历史，未补发。**E1 出口通过 ≠ 最终第二机总签收、M01—M05 总签收或
   实际投稿完成**。
2. 关联更正契约已随 PR #80/#83 合入 main（`319aa63`）；C 侧 wire 字段
   映射确认（#5 6398872018）已完成；E1 中恢复关联与追溯已实测正确（行 8）；
   第二机自然持续恢复（180s sustained）与 GuardedRecovery 针对性续验仍
   开放（旧两次降温检查失败为 `0db9402` 历史记录）。
3. 智能算法未启用：以版本化阈值规则为基线，无 M06/M07 算法报告，不虚构
   算法性能页。
4. WH1 多批告警评价：**旧 A46 hold 为当时历史定位**，不再概括当前全部
   实现——A 的 f6 同源三批分段已实测（行 15：3 条不同批次高温经真实
   MQTT 到已认证 B ASGI HTTP 落库并读回历史规则、发送器重启 0 重发），
   但**该分段是 ASGI HTTP 层，不写成三批完整 TCP E1**；三批各自完整
   TCP 端到端仍未测，如实保留。逐批规则引用已随 PR #99 `4b9bd50` 入
   发布点（§5.1）；旧 f6 包默认列空为有日期历史，不追改、不解释成
   "无规则判正常"。
5. 模拟数据不宣称为真实果品品质实验；阈值异常不宣称真实腐败概率。
6. ER 图基于业务源码 `71e5a882` 反射；冻结版本若模式变化须重新核对，
   不只替换 SHA 标签（本次架构图更新为 A 的 e7 图件，ER 反射版本未改标）。
7. 本文档双轨对齐（见汇编基准）：E1/通知实收证据源 `e7fffd61`（=
   main `319aa63`，行 8 同源）；发布点 main `1a2fa53`（= 受测应用
   `4b9bd50` 全树一致，含 PR #96→#98 通知安全修复与 PR #99 逐批证据/
   显式 CSV v2）。测试数按各自源与层级分列、不相加；正式实发仅限单个
   授权邮箱（PR #97），低层多地址解析不写成正式实发支持；M03 成品需
   三人各自实签。
