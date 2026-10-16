# C1 观测事务与幂等实现提案

状态：**C 内部设计草案，已有隔离 SQLite 事务候选，未接正式 PostgreSQL、未建迁移、未开放 API**。本文件供
S0/F01—F03 审核；正式表和字段只在 B 的唯一迁移 head 合入、共同契约确认后落地。
候选实现在 `backend/app/modules/telemetry/storage_candidate.py`，独立
SQLAlchemy `MetaData` 只供隔离测试建表；生产不得 `create_all`。它把原始观测、
每次接收与待评估任务放进单事务，含设备/消息唯一约束和接收 ID 主键。
Windows/Codex Python 3.13.9 的临时 SQLite 文件数据库执行了 8 个事务测试，
连同已有五组 C-only 测试 **86/86 OK**；实际证据在 `03-test-evidence.md`。
没有 PostgreSQL、并发唯一约束、正式迁移、A/B 服务或 E1 验收。

2026-10-15 补测：在 C 独立 SQLite 文件候选表上，两线程并发送同一业务消息但使用不同可信接收 ID，数据库锁导致的暂时失败用原封装重试后，最终只产生一条业务观测、一条评估任务，并保留两条接收证据。九项存储测试连续十轮通过，六组 C-only 合计 88/88 OK；只属于 SQLite 竞争回归，**PostgreSQL 并发唯一约束与正式应用入口仍待实测**。详情见 `03-test-evidence.md` 最新节。
固定 JSON 和隔离纯处理测试不是本设计的持久化验收；早期记录的 60 项是当时快照。2026-10-15 新库 C 代码 `e0f14c44c19597c925ca5e3ed387d8f01d1796cd` 在 Windows/Codex Python 3.13.9 五组 C-only 测试为 **76/76 OK**，接线说明受检提交为 `c08829be8cc4437a34e9f442bf606448b9cfa7ce`；完整证据见 `03-test-evidence.md` 顶部及[新 #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019)。本设计仍无正式 PostgreSQL 事务、迁移或 E1 结果。
另用共同 main `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813` 的
`iot/ingress.py` 与 C PR #11 的 `envelope_preview.py` 在隔离目录运行了
5 项 A→C **代码边界**测试：合法高温、重复/冲突、缺测/错单位/坏采样时间、
设备自报接收时间不得覆盖可信时间、身份不符拒绝，均通过。为在当前 Python
3.13 上导入，只在隔离镜像给 A 的一处多异常捕获加括号；没有修改 A 远端文件。
这个检查没有 MQTT、真实设备鉴权、PostgreSQL、服务启动或 E1 证据。

## 输入和责任

- 唯一业务入口拟为 C 自有 `ingest_observation(envelope)`。消费 A 在共同 main
  已实现的可信 `ReceptionEnvelope`，不接受设备直接调用的公共 HTTP 入口。
- A 验证设备身份、仓库绑定、UTF-8/JSON、载荷大小与深度；C 再验证最多 16 个
  metrics、key/unit 长度、UTC 采样时间、单位和数值。C 不从原文中的
  `device_id`、`warehouse_id` 或 `received_at` 建立可信身份或服务时间。
- 每次接收保留 exact `raw_text`、原 `raw_payload` 和 A 给出的
  `reception_id`、认证设备、绑定仓库、UTC `received_at`。原文、质量标记、
  规范化数值和阈值/规则投影分开保存，后者不能覆盖前者。
- `sampled_at` 属设备采样时间；乱序到达照实保存，B 规则按此 UTC 时刻选
  历史版本。非法时间保留原文和质量原因，不伪造采样时刻或正常风险。

## 拟议记录与约束（均未建表）

| C 自有逻辑记录 | 必要属性 | 唯一性与用途 |
| --- | --- | --- |
| observation | 首次有效消息的设备、消息 ID、仓库、原始内容摘要、首次接收 ID、质量与规范化投影 | `(authenticated_device_id,message_id)` 唯一；`observation_id` 在首次提交后不变 |
| reception | 每次接收的 exact 原文、可信元数据、关联的原 observation、处理结论与错误原因 | `reception_id` 唯一；同一次数据库调用重试不产生第二条接收证据 |
| evaluation work | 新 observation 待按历史规则评估的任务及版本 | 与新 observation 同事务创建；B 暂时不可用时仍可恢复评估 |
| candidate outbox | 不可变候选正文、`source_event_id`、规则快照 ID、投递状态、下次尝试时间 | 同一候选只产生一个源事件 ID；重试只读原正文，不重算 ID |

这些只是 C 模块内部的记录职责，不是已确认的公共表、字段或迁移文件。
服务不得在主观测落库成功前向 A 返回 `accepted_new`，也不得在 B 收件成功前把
候选标为已交付。

## 单次接收的事务目标

1. 校验可信封装和 C 结构边界。A 已拒绝的非 JSON/身份错误由 A 审计；
   C 检出的结构越界返回 `invalid` 和有限原因，留有界拒绝证据，不生成业务
   observation 或候选。坏时间、缺测、错单位等合法结构留原文和质量标记，
   风险为 `unknown`；是否以 `accepted_new` 保存这些记录待 S0 最终确认。
2. 以可信设备和原包 `message_id` 定位业务键。摘要包含采样内容与未知扩展
   字段；只忽略服务器每次接收时间，并规范 UTC 等价表示及唯一指标的无序列表。
   exact 原文始终另存，摘要不能代替原文。
3. 在数据库事务中利用唯一约束判定新消息、同内容重发或同键冲突。新消息只
   创建一条 observation 和可恢复的评估任务；同内容重发返回原
   `observation_id` 与 `accepted_duplicate`；同键改内容保留冲突接收证据、
   返回 `conflict`，不改原 observation、不再产生业务候选。
4. 同一 `reception_id` 的数据库重试必须复用既有结果；若同一接收 ID 携带
   不同原文，应显式报冲突并保留审计线索。数据库提交失败统一回
   `retryable_failure`，事务回滚，A 保留待发任务；不能把存储故障伪装为
   `invalid` 或清除 A 的队列。
5. 提交后由 C 评估任务按 `sampled_at` 调 B 的历史规则选择。缺规则或缺测
   保持 `unknown`，不伪造正常/恢复；B 暂时失败则保留可重试任务。有效高温
   按固定规则版本生成候选正文和 outbox；候选 `source_event_id` 一旦持久化，
   超时、查无、重投均用同一个 ID。B `accepted_duplicate` 需核对摘要；
   `conflict`/`invalid` 保留人工可追溯失败，不无限重试。

## 必须实际执行的验收

| 场景 | 最小可核查结果 |
| --- | --- |
| 新消息、重启后同键同内容重发 | 一条 observation、两次不同 reception 证据、相同 observation ID、一个评估任务 |
| 同一 reception ID 的调用重试 | 不增加接收证据；返回原结果 |
| 同键温度或未知扩展字段改变 | 一条原 observation、一条冲突接收证据、不生成第二候选 |
| 两个并发事务争同一业务键 | 唯一约束生效；返回新/重复或冲突，不双写业务观测 |
| 高温、缺温、坏单位、非法采样时刻 | 原文均保留；有效真高温可成候选；其他未知原因清晰，不判正常 |
| 模拟提交失败并重试 | 首次不回成功；重试后恰好一条业务观测 |
| B 暂停、超时、按源查询 404 | C 评估/候选任务跨重启保留；同 ID 重投，不丢候选、不重复业务事件 |
| 查询/CSV | 复用同一 actor、筛选和服务端固定结果快照，缺测不展示为正常 |

验收还需空库按 A01→B→C 单迁移 head 升级、真实 PostgreSQL、应用重启、
权限拒绝和 E1 一间冷库链路。上表是正式验收目标；下面独立测试专用
PostgreSQL 检查不构成正式迁移、应用接线或 E1。

## 需 A/B/集成方确认

- A：合法结构但非法采样时间/单位由 C 保存为带质量标记的 observation，
  还是只保存 reception 并回 `invalid`；A 待发队列何时清除。设备原包若含
  `received_at`，摘要中忽略该字段的范围需写进 F01。
- B：F02 规则选择发生暂时数据库故障时必须可重试；连续三版 append-only
  规则应可插入并按历史时间选中。见
  [PR #10 C 静态审查](https://github.com/Cynthia060913/freshlink-zhikong/pull/10#issuecomment-6142448128)。
- B：F03 `source_event_id`、`rule_snapshot_id` 与规范内容摘要映射；
  `timeout → by-source 404 → 同 ID 重试` 的五类回执及永久拒绝边界，参见
  `examples/candidate-f03-rc1-proposal.json`。接收成功不等于通知实收。
- 集成方：B 的唯一迁移 head、C 记录命名/索引、根 API 和权限接线，以及 F06
  固定快照/CSV 的资源容量。C 不单独改共享根路由、导航、Compose 或他人迁移。

## C 自有测试专用 PostgreSQL 回归（2026-10-16）

`backend/tests/modules/test_telemetry_storage_postgres_candidate.py` 是显式开启
的 C 候选事务检查。默认跳过；只有环境变量
`FRESHLINK_C_TEST_POSTGRES=1` 且现有隔离测试运行器已提供
`DATABASE_URL` 指向 `test-db/freshlink_test`、`PROJECT_NAME=FreshLink-test`
时才连接。三项 guard 在连接前执行；绝不能改成宽松的“任意 PostgreSQL”。
测试在该测试数据库创建随机 `c_test_<uuid>` schema，仅在其内用 C 独立
metadata 建候选表，结束按受限名称删除自建表与空 schema，不用 `CASCADE`。
这**不是**项目启动时 `create_all`、正式迁移或共享业务表。

在已启动的测试专用 PostgreSQL 容器内、从 `backend` 运行：

```text
FRESHLINK_C_TEST_POSTGRES=1 python -m unittest tests.modules.test_telemetry_storage_postgres_candidate -v
```

目标检查 exact raw、高温/质量、同接收 ID 重试、跨接收重复与冲突、评估任务
写入故障整事务回滚、两写者争同键、全新数据库连接回读 3/6/3 行及原文。
新增一个**尚未在本人环境实际运行**的 PostgreSQL 两事务回归：T1 先写入
较早采样/接收时刻的观测但不提交；另一连接固定旧快照后 T1 才提交，旧快照
列表与 CSV 均排除该观测，新快照均包含它。直接 SQL 插入只用于构造这个
未提交交错，不代表 A→C 应用接收已实现；同样只操作上述随机测试 schema。
新连接回读不是 PostgreSQL 进程重启；正式重启保留仍需另测。C 当前 Windows
执行环境无 Docker 可执行程序和 psycopg，本轮只完成模块导入/默认跳过、
SQLite schema 翻译预检与 C-only 回归，**未实际运行此 PostgreSQL 测试**。
A 在 [#9 独立报告](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6195386092)
的测试专用 PG18/重启结果是外部证据，不能代填成 C 此测试的实跑结果。
