# 鲜链智控软件设计文档（M03 汇编稿）

**汇编**：B（xixidamei），2026-10-28 修订。
**汇编基准**：共同 main `319aa63`（含关联更正契约 PR #80/#83 与 A PR #94
已合入成果）；B 增量 PR #96（SMTP 固定阶段诊断，head
`5fa963c45f9e7a97e78856440e00998f24693aa8`）引用处单独标注，未把增量写成
已发布事实。
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

图：[系统架构图](design/tikz_architecture.png)（可编辑源
[tikz_architecture.tex](design/tikz_architecture.tex)；通知实收链为虚线，
标注"待验收"，与当前真实状态一致）。

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

### 5.4 关联数据质量更正（F03 契约，PR #80 待审增量）

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
  封顶；permanent 不重试。
- **尝试审计**：每次尝试（含失败）追加 `alerts_notification_attempt` 行；
  独立 attempt session 提交（或在 SAVEPOINT 内），**审计失败绝不 rollback
  业务事务**，只在结果 detail 如实标注。
- **同源去重**：同一 (事件, 渠道) 已有 delivered 记录则跳过；去重查询自身
  失败时**保守 fail-closed 零发送**（`dedup_error=True`）。
- **渠道隔离**：单渠道违反契约只影响该渠道，异常文本/URL 不入库（固定
  错误码）。
- **只消费已提交事件**：`dispatch_committed_event` 在业务事务提交后由
  worker 调用；网络与退避 sleep 不进业务事务、行锁或 HTTP 请求内。

### 5.6 持久派发 runner（`notifications/runner.py`）

- **Pending 规则**：事件 pending ⇔ 至少一个配置渠道缺 delivered 尝试行，
  总尝试数 < `max_attempts_per_event`（12），最新尝试早于
  `min_attempt_age_s`（60s，重启不清零的持久退避——预算在表里，不在内存）；
  oldest-first，部分送达的只补缺失渠道，不重复打扰。
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
| 1 | B 告警全量套件（含更正 21 条、runner、派发、恢复、迁移、SMTP 阶段诊断负向 6 条） | 真实 PG 18 / Python 3.14，本机隔离实例 | 400 passed, 2 skipped（PG opt-in）, 15 subtests | `backend/tests/modules/alerts/`，PR #96 头 `5fa963c` 工作树实跑（2026-10-28） |
| 2 | 更正契约回归：首收/重放/冲突/未知原异常/跨批/更正先于恢复/旧恢复重放/DTO 拒绝 | 真实 PG | 21 passed | `tests/modules/alerts/test_data_quality_correction.py`（PR #80 组合头 `d1f5359`，已入 main） |
| 3 | 双连接锁交错：更正排队于原异常行锁后，不回退并发提交的 resolved | 真实 PG 双连接 | passed | 同上（`test_correction_serializes_behind_held_original_row_lock`） |
| 4 | ACK/恢复行锁串行化：双确认只留一条、恢复不回退 | 真实 PG 双连接 | passed | `tests/modules/alerts/test_recovery_candidates.py`（A 审入 ae2a941） |
| 5 | runner 互斥/持久预算/dry-run/重启续跑 | 真实 PG | passed（互斥为双实例真实争锁） | `tests/modules/alerts/test_dispatch_runner.py` |
| 6 | 全量后端回归（CI） | GitHub Actions Python 3.14 / 隔离 PG | PR #80 组合头 `d1f5359` CI 9 workflows 全绿；PR #96 `5fa963c` 结果见 PR checks（本稿修订时以 CI 实况为准） | PR #80 / PR #96 checks |
| 7 | A 组合链：14 设备/7 流/执行 ACK 后降温、离线重启恢复、CSV 对拍 | 精确 `62bb2ef` 受控环境 | 见 A46 记录 | [A-TEST-REPORT-CANDIDATE.md](A-TEST-REPORT-CANDIDATE.md) |
| 8 | A 通知实测（#8 6440797666）：单一事件、每渠道一次、失败不补发 | A 本机、真实渠道 | **飞书手机端实收由 A 本人确认**；SMTP 一次发送遇 SSL 意外 EOF，**终端实收待核，未补发** | #8 6440797666（A 队列记录） |
| 9 | C 第二机（PR #11 精确 `0db9402`）：默认 CMD/HTTP 与 14 设备分段 | C 第二台电脑 | 默认 CMD/HTTP 与 14 设备分段通过；**GuardedRecovery 降温检查两次失败，未最终签收** | PR #11（C 测试材料） |

**如实未测**（不写成通过）：SMTP 邮箱终端实收（一次 SSL EOF 后待核、未
补发）；C 自动恢复发送门禁最终签收（两次降温检查失败）；完整同版 E1；
最终第二机复现冻结版；runner 组合进 A 运行包的现场验证；
SIGTERM 派发中途窗口。

## 10. 限制与未验证清单

1. 通知终端实收：**飞书手机端已由 A 本人在受控单一事件实测确认**（#8
   6440797666）；**SMTP 邮箱一次发送遇 SSL 意外 EOF，终端实收待核，未补发**
   ——服务接受 ≠ 实收，输出字段固定 `terminal_receipt_confirmed=False`；
   完整双渠道 E1 链路仍未完成。
2. 关联更正契约已随 PR #80/#83 合入 main（`319aa63`）；C 侧
   c-data-quality-correction/v0 → wire 字段映射确认（#5 6398872018）与
   GuardedRecovery 恢复链针对性续验（#6，两次降温检查失败）仍开放。
3. 智能算法未启用：以版本化阈值规则为基线，无 M06/M07 算法报告，不虚构
   算法性能页。
4. WH1 多批告警评价在 A46 记录中仍 hold；多批完整链路待同版 E1。
5. 模拟数据不宣称为真实果品品质实验；阈值异常不宣称真实腐败概率。
6. ER 图基于业务源码 `71e5a882` 反射；冻结版本若模式变化须重新核对，
   不只替换 SHA 标签。
7. 本文档对齐汇编基准 main `319aa63`（+ PR #96 增量）；冻结候选确定后
   须逐节复核更新，M03 成品需三人各自实签。
