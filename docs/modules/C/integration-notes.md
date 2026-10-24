# C 共享接线请求

## 2026-10-23 F02 真实规则种类到 F03 事件种类映射

核对本仓 B `services/rules.py::select_rules_for_observation` 的实际返回，
F02 `RuleSelectionEntry.kind` 是规则候选类型 `threshold_high`，
而 C→B F03 冻结异常正文的 `kind` 是 `temperature_high`。C 的高温评估
和历史恢复规则绑定现严格消费前者，继续产生后者；两者不可直接等值比较，
也不修改 B 的 F02 字段。C 自身合成选择器样例/测试同步为真实 F02 值。
`rule_evaluation_candidate.py::LocalBRuleSelectionCandidate(engine)` 用独立
短 Session 调实际 B 服务并输出 `model_dump(mode="json")`，在 A/C 写锁外完成
规则读取；读库失败向外抛以保留待评估重试。该适配器不选择可信批次、不
安装调度。新跨模块测试使用 B 真正的仓库/批次/快照服务和 C 观测/异常/
恢复规则绑定；A worker 调用、正式发送和 E1 仍须同版验证。

## 2026-10-23 恢复候选同事务门禁入口（未接 worker/发送）

`recovery_outbox_candidate.py::stage_gated_recovery_candidate(engine, *,
device_id, warehouse_id, stream_epoch, original_source_event_id,
historical_rule, now)` 要求调用方在进入时已经取得 B 原异常**历史**规则；
本函数内部先以 A `begin_evidence_write` 锁可信设备，复核 C 已存恢复窗口，
再用 A `read_complete_frontier` 取写后完整戳，并在**同一 Connection/事务**
调用既有 `freeze_recovery_frontier_on_connection_candidate`。成功一次提交
A revision、C 恢复 outbox 与六个冻结字段；前沿不完整时整体回滚、保守 hold。
重复/未到期/等原异常或规则时回滚无用 revision；来源正文冲突时保留 C
冲突审计并用 A `mark_uncertain` 保守保持设备 hold。锁内没有 B 查询、HTTP 或
通知发送。旧 Engine 单独恢复入口继续存在，但不能用于正式自动恢复。

此为显式调用候选，**未安装轮询 worker**。本机 Windows 隔离 Python3.13.9
SQLite Mock-A 新增门禁/冻结与前沿不完整回滚两例，文件全套 22/22；
本小包精确 `a6bc1121c84b29766556d8dc194d7095fbc0d457` 另在受控远端
[A–C strict run 39898732584](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39898732584)
以真实 PG/A 门禁跑新增冻结/重放测试，文件两项通过、全 1134/0跳；
B `delivered` 状态和历史规则仍是夹具。本机 SQLite Mock-A 不证明 PG 锁；
远端专项也不证明所有 C 直写入口覆盖、实际 B 接受、发送前戳复核、
重启/并发完整业务或 E1。A/C 仍须在唯一已审 A05→C01 组合下跑这些
同版场景，并由 B 确认已受理后更正语义。
恢复自动外发继续关闭，候选和测试不得记为完整 E1。

## 2026-10-22 恢复冻结戳持久字段候选（未启用投递）

`outbox_candidate.outbox` 与非活动 `migration_candidates/c01_telemetry_storage.py`
现包含六个可空的 A 前沿字段：`frozen_device_id`、`frozen_warehouse_id`、
`frozen_stream_epoch`、`frozen_admitted_seq`、`frozen_settled_through`、
`frozen_evidence_revision`。原异常 ID 沿用 `related_source_event_id`，不另造
第二个可能不一致的关联字段。异常候选和旧独立恢复候选这些列为 `NULL`；
**NULL 绝不是自动发送许可**。表仍为非活动提案，不能直接升级原库。

新增 `freeze_recovery_frontier_on_connection_candidate(connection, *,
recovery_source_event_id, original_source_event_id, stamp) -> "frozen"|"duplicate"`。
调用方必须在同一外层 PostgreSQL READ COMMITTED 事务里先按 A
`begin_evidence_write` 取得同设备写门禁、核 C 恢复窗口并暂存恢复候选，最后读取
写后的完整 `FrontierStamp` 才调用此端口。端口本身再以 A 的
`stamp_is_current` 持 stream 锁重核完整前沿，核恢复与已送达原异常、两端已存观测
的设备/冷库归属，写六字段；同戳重放为 duplicate，变戳/部分字段/错关联失败并由
调用方回滚。不自行提交、不发 B、不把坏证据解除 hold。

本人隔离 SQLite 单测用**Mock A 前沿判定**验证字段回滚、提交后重开、同戳重放、
变戳与错原异常拒绝；另在严格 opt-in PG 文件增第10项验证 C 字段对第二连接
提交前不可见、回滚撤销和新连接读回，**本人无 PG，该项目前只运行并跳过**。
两组都 Mock A 前沿判定，不证明真实 PG 设备锁。A/B 集成前还需真实 PG 同设备
写/冻结并发、所有 C 写入口门禁、晚到坏证据失效、发送前复核与已受理后更正
语义；恢复自动外发继续关闭。此字段小包不替代正式 C01 唯一链提升。

## 2026-10-22 温度高候选与评估状态同事务端口

`rule_evaluation_candidate.py::stage_evaluated_high_on_connection_candidate(
connection, observation_id, batch_id, body, now)` 接收调用方在锁外依据 B 的**历史**
规则预选的完整 F03 正文。调用方须先在同一 PostgreSQL 事务取得 A 的
`begin_evidence_write` 设备门禁；本端口重读待评估的已存 C 观测，核对确定性
`source_event_id`、批次/冷库/时间/数值/质量及高温阈值，然后在**同一 Connection**
从已保存原文重算质量与候选证据，核对设备/消息 ID 和原始单位/数值，
再冻结 outbox 正文并标记任务/风险。外层回滚同时撤销三者；重试相同正文去重，
同来源异文只留冲突审计且不把风险标正常/候选。无 B 网络请求、自行提交或发送。
这只解决单批温度高的局部原子写入；B 历史规则的可信选择、批次授权/重新核对、
A worker 的真实调用、其他 C 写入口门禁及恢复冻结戳仍待共同接线与真实 PG 验证。
旧 `evaluate_one_batch_candidate(engine, …)` 保持兼容，仍非同事务流程。

## 2026-10-22 非活动 C01 十表迁移提案（不得直接升级原库）

C 已在 `backend/migration_candidates/c01_telemetry_storage.py` 准备**非活动**
Alembic 源码，提案父节点为 A [PR46 候选 `a05_stream_frontier`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/46)。
它不放进 `backend/app/alembic/versions`，不改 `env.py`、共享 head、主程序或真实
数据库。十张表与 C 当前九张 `storage_candidate.metadata` 加一张
`recovery_progress_candidate.metadata` 的列、空值、类型、外键、唯一约束、
两索引逐项一致，升级不插入数据/epoch/hold；本机静态及隔离模型测试 3/3。
表名仍带 `_candidate`，因为当前 C 运行源码直接使用这些表名；团队若要在发布时
命名去后缀，需同一包修改模块表映射与迁移并跑空库/旧数据/回滚测试，不能只改迁移。

A 作为唯一集成方应在 A04/A05 真正审定并合入唯一链后，先核对当时真实 head、
C metadata/表名及恢复冻结 stamp 必需字段，再决定是否提升这份提案为活动后继，
同时登记两个 C metadata。当前提案虽补冻结 stamp，原异常关联沿用
`related_source_event_id`，但全部写入口和发送线性化仍未接通，不能直接当作
自动恢复可发布 schema。
需真实 PostgreSQL 空库→唯一链升级、保留 A/B 旧行、`alembic check`、
重启留存及降级边界验证；本机 3 项只证明源码静态一致性。

## 2026-10-22 评估结果同连接写端口（局部准备）

`rule_evaluation_candidate.py::mark_evaluation_on_connection_candidate`
将 `evaluation_jobs` 状态及 observation 风险写在调用方已开启的同一事务内；
不自行提交或向 B 查询规则。旧 `evaluate_one_batch_candidate(engine, …)`
仍在锁外选择 B 历史规则，并使用独立 Engine 事务冻结 outbox 与标记结果，
**尚未整体转换为 A 门禁内原子流程**。正式接线要先在短事务外查询 B 规则，
再在持同设备门禁的事务内重读 observation/归属/规则版本、调用已有
`stage_f03_on_connection_candidate` 和此标记端口；不能在锁内执行 B HTTP，
也不能把本局部函数测试算作候选自动送达。C 本机隔离该组 7/7 通过。

## 2026-10-22 恢复窗口同连接入口（已准备，未启用自动恢复）

`recovery_progress_candidate.py::advance_persisted_recovery_window_on_connection_candidate(
connection, initial, rule, sample)` 在调用方外层事务内读写原异常的连续恢复窗口，
不自行提交、重试或开另一连接。调用方须先持 A 同设备写门禁，再用这个
Connection 核对持久观测/历史规则并推进；数据库错误或 CAS 冲突直接向外抛，
由调用方整体回滚。旧 `advance_persisted_recovery_window(engine, …)` 保留独立
重试行为，但不持 A 锁，不能用它宣布自动恢复已经接线。C 本机 SQLite 恢复
相关 23/23 通过，严格 PG 文件共 8 项本机均跳过，新第8项已编写等待受控环境
实跑。这一步尚无冻结 stamp、原异常关联、晚到坏证据失效或 B 接收线性化，
自动恢复外发继续关闭。

## 2026-10-22 观测同连接入口（已准备，未挂正式 A 门禁）

C 的 `storage_candidate.py::ingest_observation_on_connection_candidate(
connection, envelope)` 已在 C 分支准备。调用方须先开启外层事务，并在**同一
Connection** 按 A [PR45 `f52e495`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/45)
候选的 `begin_evidence_write(connection, device_id=…, warehouse_id=…,
epoch=…)` 获取设备写门禁，再调用 C 入口；本函数不自行提交、回滚、重试或
调用 B。有效观测的原文/质量/评估任务及 invalid、消息冲突、reception ID
异文复用审计都受外层事务控制。唯一约束/数据库错误直接抛给调用方，调用方须
整体回滚；成功返回仍只是暂存，不能在 commit 前回复 A 已持久交付。
原 `ingest_observation(engine, envelope)` 独立入口保留兼容性，**不持 A 锁**，
不能作为 A24 正式 worker 的交付端口。A 集成时要在已确认设备绑定/epoch 的
同一事务里替换调用点，不能先写 C 再补门禁。C 本机 SQLite 13/13，新增
严格 PG 第7项仅已编写、未在本人本机运行；A 的 PR45 端口及活动 A05、C
后继均需独立审核/发布。此增量未触及规则评估、恢复窗口、冻结或发送端口，
自动恢复继续关闭。

C 已对 [PR44 精确 `a35a1e3`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/44#pullrequestreview-5607772122)
提交共同基础候选批准；仅 A03→B01→A04 范围，不覆盖 PR45—53 或正式 C 表。

## 2026-10-21 A24 后 C 必须补的最小写门禁（提案，尚未实现）

A [PR43 `a5f27e6ffe7d56844aa388dbedef2ba2a06ee9ee`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43)
提供了**默认未启用**的 `read_complete_frontier(Connection, device_id,
warehouse_id, epoch) -> FrontierStamp | None` 与
`stamp_is_current(Connection, stamp) -> bool`。C 已[按精确头批准该候选范围](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43#pullrequestreview-5598813842)。
这不是对任何 C 写入或自动恢复的放行。为避免只在定时器外层检查而遗漏
直写入口，正式接线须在以下**同一 PG READ COMMITTED 事务**中检查：

1. `storage_candidate.py::ingest_observation` 的 `observations`、`receptions`、
   `reception_reuse_conflicts`、`quality_tags`、`evaluation_jobs` 写入，尤其
   invalid/冲突没有新 observation，仍要使同设备恢复前沿失效。A worker 调 C
   不得先用未锁定的 C 事务提交、随后才取得流锁。
2. `rule_evaluation_candidate.py::_mark/evaluate_one_batch_candidate` 的风险和
   `outbox_candidate.py::stage_f03_candidate`，以及
   `recovery_progress_candidate.py::advance_persisted_recovery_window` 和
   `recovery_outbox_candidate.py::stage_eligible_recovery_on_connection_candidate`。
   现有 Engine 包装器各自开事务，不能当成已持 A 锁。规则服务 HTTP 调用必须
   在短事务外完成，入库前按原采样时刻重核规则与观测归属。
3. 冻结恢复时，持 A 流锁取得 `FrontierStamp`，在同一 Connection 校验原异常
   所属设备、冷库、世代和已存观测，然后将 `stream_epoch`、
   `admitted_seq`、`evidence_revision` 与原异常 `source_event_id` 一起持久关联
   恢复候选。现在 C outbox **没有这些字段**；迁移只能在 B01→A04→C 的唯一
   已合并链后由 A 协调，不在本分支另开平行正式 head。
4. `delivery_candidate.py::deliver_once_candidate` 当前只检查 B 受理摘要和
   原事件 resolved，不读取 A 前沿。自动恢复发往 B 前须以同一设备锁复核
   冻结戳、C 坏接收及规则/绑定版本，失效则持久停发并标记待人工复核；晚到
   invalid/冲突即使 `sampled_at` 较早，也不能被旧窗口跳过。需要 A/B 一起确认
   **复核与外部 HTTP 之间的线性化/更正语义**；不在锁内做网络请求，也不能仅凭
   一次无锁预检宣称消除竞态。

当前可审核出口：A 提供已审锁入口与正式运行调用点；C 提供同事务写端口和
失效标记，测试同设备并发写/冻结、晚到 invalid/冲突、重启后戳回读、发送
超时重试与历史规则更换；B 确认已受理恢复之后的更正/重开语义。上述结果
未完成前自动恢复外发保持关闭，独立候选测试不能记作 E1。

## 2026-10-21 最新对接状态（独立候选，未发布主程序）

- A [PR40 `e69094ff2c2bf81790a7402befdd8b18066d7c3c`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40)
  在分层候选接入 C 最新读取和管理员拒收审计路由；A 受控 PG18.6 实跑当时
  C 原有 5 项严格 PG 专项。C [按该头批准接线范围](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40#pullrequestreview-5594554218)。
  此挂载仅在 A 候选分支，不代表 main 已含正式 C 表或本分支独立启动即挂载。
- B 交接后 A [PR42 `6ed501f13fd668412ea999b618a97ba188f7db1e`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42)
  的 ACK/恢复共同锁候选经 C [精确头批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42#pullrequestreview-5594935429)，
  尚待唯一迁移链整合；A 的 A24 接纳前沿/晚到失效与 C 第 6 项严格 PG
  恢复事务用例仍独立待验。**自动恢复及外部投递保持未接线**。
- C 当前 [PR11 `bf991a0c610dd91cc89be950af15ae020f711bd5`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  已提交新增第 6 项严格 PG 回归和 outbox 类型修复；后者本机文件级
  mypy strict/ty 通过。A 若开始正式组合，请以本人最新头重做差异核对，
  不将 A 先前 f895 组合结果外推到此头。

## 2026-10-21 A24 恢复冻结的 C 同连接事务端口候选

回应 A [A24 前沿与冻结提案](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/37)：
C 在隔离模块新增
`stage_eligible_recovery_on_connection_candidate(connection: Connection, *, original_source_event_id: UUID, historical_rule: dict | None, now: datetime)`。
调用方先开始事务并按 A 审核后的设备流协议取得同一设备锁；C 在**该连接**
读取原异常、已存坏接收、持久恢复窗口和原观测，再冻结 F03 恢复正文及 outbox。
底层 `stage_f03_on_connection_candidate` / `stage_candidate_on_connection_candidate`
同样不自行提交、关闭连接或调用 B；唯一键重放通过嵌套 SAVEPOINT 分类，外层事务
仍由调用方控制。SQL 数据库错误向外抛，调用方必须整体回滚并按真实错误分类。
原有 Engine 入口仅是自有事务的独立候选包装，不持有 A 锁；不能把它用于 A24
正式冻结。隔离 SQLite 测试证明外层回滚同时撤销原事件送达状态与恢复候选，
并证明重复/冲突 savepoint 不擅自提交。**尚未在正式 PG/A 同设备锁下验证**。
现有 C 严格 opt-in PG 文件另新增同连接 SAVEPOINT/外层回滚/重连回读用例，
保持 `test-db/freshlink_test/FreshLink-test` 与逐项随机 schema 守卫；本人
Windows 无受控 PG，本机只收集并跳过，不把它写成 PostgreSQL 已通过。

此端口只是 A24 第三个原子边界的 C 侧准备；并没有实现 A 的接纳序号、
连续终态前缀、坏证据修订、发送前复核、同设备所有 C 直写入口门禁或晚到
已发送后的更正策略。多个批次须以各自原异常 ID 独立持久窗口/来源 ID 冻结，
不能从一设备或一批次的正常值推断同仓其他批次恢复；绑定/规则版本变更必须
使尚未投递的候选保守失效，具体跨模块字段和 B 更正语义仍待审定。A/C/B 在
同版 PG 中跑负向向量前，**持续自动恢复及外部投递保持禁用**。若 A 要接线，
请提供经过审查的锁取得位置、A 前沿查询签名及共享事务调用点；C 将按唯一链
和确定字段补锁后复核，不在此草案中改公共模型/迁移。

## 2026-10-21 最新已存观测轮询候选

C 独立路由 `GET /telemetry/observations/latest` 至少需要可信设备或冷库 UUID，
按 `(first_received_at_utc DESC, observation_id DESC)` 返回一条**新业务观测**及
服务器查询 UTC；重复接收不顶替最新观测。此读取不创建历史快照，空库返回
`item:null`、缺筛选 422、存储故障 503/`Retry-After: 30`、响应不缓存。
独立 C 页面在用户提交设备/冷库筛选后每 10 秒查询，超过 60 秒或时间非法
提示数据过期、当前风险未知；无值、无规则和服务失败也不显示正常。历史分页
及 CSV 仍读固定快照，不让轮询耗尽快照容量。`sampled_at` 是设备时间，
`received_at` 是可信首次接收时间；本候选按后者排序，不构成 MQTT 流订阅或
批次级规则结论。仅在 C 测试应用挂载；A 须待正式唯一迁移链和权限审查后
决定共享根接线。正式验收还需主程序页面、实际 PG/重启、两库多设备及断网恢复。

## 2026-10-21 拒收接收证据的受限审计候选

`reception_audit_candidate.py` 只在 C 模块内提供未挂主程序的
`GET /telemetry/receptions/rejected?device_id=<UUID>&warehouse_id=<UUID>&limit=1..50&after=<UUID>`。
当前应用尚无冷库级读取 ACL，精确原文只能由已登录的 superuser 读取；普通
用户 403，缺失/无效身份沿用公共认证失败。结果包含没有形成 observation 的
`invalid`、同消息异文 `conflict`，以及 `reception_id` 异文复用审计；后者在原
设备/仓和后来声称方两边均可查到。每条返回可信接收身份、接收 UTC、原文、
原因及原 reception ID；分页只按审计行 UUID 稳定游标，不是时间顺序或冻结
快照。若查询过程中新增行的 UUID 排在游标之前，需要从空游标重新审计。

这只是 C 隔离接口候选，尚未挂共享根路由、页面或正式迁移；不表示全部拒收
证据已经在普通用户页面可见，也不能把空页解释为设备正常。A/B 若要给非管理员
展示，须先确定冷库授权范围、原文脱敏边界和页面入口。正式接线时应在唯一迁移
链和实际 PostgreSQL 上验证索引/响应上限、权限、重启保留及同时新增行行为。

## 2026-10-20 观测详情的接收证据展示边界

C 独立遥测页现把已有详情 DTO 中**与该 observation 关联**的接收时间、
结果和原因逐条展示；出现冲突/无效结果时提示：上方风险只属于首次观测，
不能据此把冲突接收判正常。没有修改公共导航、根路由或详情字段。
当前详情查询只返回 `receptions.observation_id` 指向该观测的记录；没有形成
observation 的 `invalid` 接收，以及同一 `reception_id` 异文复用写入的独立
`reception_reuse_conflicts` 原文，**不会出现在此详情**。详情现只对关联
接收 ID 的异文复用显示计数和风险警示，精确原文仅管理员审计候选可查。
因此页面不能声称已
完整展示所有坏接收，也不能用详情为空证明没有冲突。若 A/B 需要按设备/冷库
查看全部拒绝证据，须另审定有界、鉴权的 C 接收审计接口及页面入口；在此之前
原文保留在 C 候选表，恢复扫描仍独立按已存证据保守阻断。

## 2026-10-20 恢复证据缺口与保守边界（C 候选）

C 对 A [PR #36](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/36)
精确 `3379c06a05828a47059844f8ce91c415782aa92f` 的同设备未结前驱阻挡
已独立批准；这只减少队列内越序，不提供 C 已提交前沿。当前 C 扫描游标用
`(first_received_at_utc, observation_id)`，与 A `(received_at, reception_id)`
不是同一顺序键。过期调用正在 C 提交、后来才接纳更早可信时间、相同时间的
不同 UUID、A 已有终态但 C 未形成新 observation 的 `invalid/conflict`，仍可能
让三条好样本跨过一条坏证据。**持续自动恢复及向 B 投递须保持未接线。**

C 本轮对**已持久在 C** 的 `invalid/conflict` reception 和 reception ID
异文重用增加保守核对：原异常可信接收时刻起，只要同设备/仓存在此类坏证据，
已存观测扫描及恢复 outbox 冻结均返回 `held_invalid_evidence`，不删除原文、
不改原异常、不宣告正常。全局 reception ID 若被另一设备或冷库异文重用，
原接收方与后来声称方都按坏证据核对，不能只冻结后来声称方。时间比较先解析
UTC，不用宽度不一的 ISO 字符串
字典序；候选只扫 C 自有接收表，尚非有界生产查询。这不能发现仍在 A 队列、
尚未提交到 C 的前驱，也不能抵御检查与冻结之间的并发插入；测试在隔离
SQLite，未构成 PG 事务/锁、正式迁移或 E1 结论。冻结后才到达的坏证据
还需正式补偿/人工处理设计，不靠这项查询自动撤回 B 告警状态。

建议 A/C 最小正式方案由 A 在入队事务中给每设备分配**单调接纳序号**，重试
沿用原序号；A 的可审计 `committed_through(device_id, sequence)` 仅越过有 C
持久回执的连续前缀，`invalid/conflict` 也作为未知/中断证据交 C，不能仅因
A 删除 inbox 行视为恢复正常。C 的 reception 与观测保留该序号，恢复窗口
按其推进并在冻结时复核前缀、原始证据及多批次身份；重放只消费一次。
这涉及 A→C 公共字段/唯一迁移及跨事务原子性，**目前只是 #5 待确认提案**。
在证明前沿、并发与晚提交补偿同版通过前，安全默认是 hold 自动恢复；可继续
独立测试原始数据、质量、历史/CSV 和 C outbox 的非恢复部分。

## 2026-10-20 已存观测单窗口有界扫描候选

`recovery_observation_scan_candidate.py::scan_saved_recovery_observations_candidate`
以原异常 `source_event_id`、可信 B 批次 ID 和原异常时刻的历史规则为输入，
从 C 已存原始观测按 `(first_received_at_utc, observation_id)` 排序，每轮最多
处理 100 条。恢复窗口的可信接收水位即持久扫描游标：进程/连接重开后重入，
已处理行不会重复增加样本；缺/非法设备采样时间只按未知质量重置一次，不判正常。
缺规则不推进，存储故障可重试，原异常或规则证据冲突时阻断；合格后交现有
`stage_recovery_page_candidate` 冻结恢复候选，仍需 C outbox 与 B 来源回读。

本入口仅手动调用、每个原异常需单 worker 串行执行；尚无后台调度、跨进程
领取/锁、同观测多批次评价或正式 PG 迁移。A/C 正式接线必须验证 A inbox
对同一设备的可信接收顺序及晚提交处理：如果较早 `received_at` 的观测在较晚
观测之后才入库，当前水位会把它视为迟到并拒绝用来证明恢复。须选定串行
提交或可审计的提交序号/补偿策略后，才能宣称持续自动恢复。

## 2026-10-20 B F02 读取失败的 C 候选处理

`rule_evaluation_candidate.py::evaluate_one_batch_candidate` 消费注入的
`F02SelectionPort` 时，将数据库读取故障、权限/认证暂不可用、超时和连接中断
分别返回 `retryable_failure`，保留 evaluation `pending`、原始观测和未知风险，
不生成 F03 候选；恢复后仍对同一观测和可信批次重试。非法规则 envelope
继续阻断为 `held_unknown`，明确 `no_rule` 仍保持未知，不把它们当暂时故障。
本次只调整 C 隔离消费端口和回归；B 的正式 HTTP/数据库异常映射及 A 的
持续任务领取仍需同版接线测试。多批次同观测需独立 evaluation 状态，当前
`telemetry_evaluation_candidate` 仅以 observation ID 为键，不适合扩大范围。

## 2026-10-20 可信接收顺序水位（C 隔离候选，待正式迁移）

`recovery_observation_candidate.py` 从已存 C observation 读取 A 可信
`first_received_at_utc`，与 observation UUID 组成稳定顺序键。
`recovery_progress_candidate.py` 在原异常窗口内持久记录
`last_received_at_utc` 和 `last_received_observation_id`；窗口按此键逐条推进，
较早或同一观测的重放不能在重启后再次清空后来积累的恢复证据。
即使设备 `sampled_at` 非法/缺失，该行的可信接收水位仍会被记录，质量保持未知且
本次恢复窗口重置一次，不计入三条合格样本。此处不改变原始观测或高温值。
调用方须按 `(first_received_at_utc, observation_id)` 升序扫描；同刻使用 UUID
稳定打破平局。没有可信接收时间的旧纯判据仍可单独测试，但已写入水位的窗口
拒绝再用缺少 `received_at` 的样本更新。正式 C 十表迁移需要包含这两列；
本次仅更改隔离候选 metadata/服务/回归，未在共享 Alembic 激活迁移、调度或
主程序接线。尚未实现跨窗口的持久扫描游标及多批次独立 evaluation 状态。

## 2026-10-20 当前 C→A 正式迁移交接清单（待 B01 安全复审）

C 候选共有十张表，不能只导入 `storage_candidate`：其中九张属于
`storage_candidate.metadata`（`telemetry_observation_candidate`、
`telemetry_quality_tag_candidate`、`telemetry_reception_candidate`、
`telemetry_reception_reuse_candidate`、`telemetry_evaluation_candidate`、
`telemetry_candidate_outbox_candidate`、
`telemetry_candidate_outbox_conflict_candidate`、
`telemetry_snapshot_candidate`、`telemetry_snapshot_member_candidate`），
另有 `recovery_progress_candidate.metadata` 的
`telemetry_recovery_progress_candidate`。前九张中，outbox 两张要导入
`outbox_candidate`、快照两张要导入 `snapshot_candidate` 才会登记到共享的
候选 metadata；集成时应以实际 metadata 列表重新核对表数、外键、索引和
唯一约束。两份 metadata 都须进入 Alembic 的比对视图，避免 `alembic check`
把未登记表误判为多余对象。
正式 C revision 只能接审查并合入 main 后的唯一 B01 head，由 A 协调
`backend/app/alembic/env.py` 登记；不能从 PR #14 的旧 B856/C0dca 组合或
测试 `create_all` 宣称生产迁移已验证。空库升级、A/B 旧数据保留、模型一致、
重启留存、重复/冲突和失败回滚须在同一发布候选上分别实测。

恢复扫描的历史 B 规则端口如在 SQL 读取时发生 `SQLAlchemyError`，C 当前将
单个窗口记 `retryable_failure/rule_storage_unavailable`，保留原窗口和同一来源
ID，继续处理页内其他窗口；下一轮从空 cursor 重扫。此行为仅在隔离端口测试，
尚无真实 B 查询或生产调度证据。

## 2026-10-20 已持久恢复窗口 → 冻结 F03 outbox（隔离接线候选）

`recovery_tick_candidate.py::stage_recovery_page_candidate` 是 C 自有的**单次**
有界扫描入口：按原异常 UUID 稳定分页，只处理已持久且 eligible 的窗口；
原异常尚未由 B 回读确认为 delivered 时不查询规则。调用方注入
`HistoricalRecoveryRulePort.for_original(original_source_event_id,
anomaly_sampled_at)`，返回 B 在**原异常采样时刻**的规则选择（无规则返回
`None`）。规则查询拒绝/超时分类为可重试；无规则保持 waiting_rule。
`next_after` 可扫完本轮，下一轮必须从空 cursor 重扫等待项；即使单项等待也
不会遮住同页后续窗口。此入口不安装后台调度、不调用真实 B、不发送 outbox，
不承诺自动告警恢复。A 的共享运行入口和 B 历史规则适配在正式链就绪后接入，
由 C 补同版 PG/HTTP/断线重启证据。

`recovery_outbox_candidate.py::stage_eligible_recovery_candidate` 读取 C 已
持久的原温度高异常、连续恢复窗口及窗口中每条原始观测，并消费 B 在**原异常
采样时刻**对应的历史规则选择。只有本地原异常经 B 来源回读确认 `delivered`、
冻结规则一致、至少三条同设备/仓且质量 `ok` 的观测与时间水位一致时，
才用原异常 ID 派生稳定恢复来源 ID，生成保留原快照/版本/severity 的 F03
正文并调用 C outbox 冻结。中途重开再调用是同来源 ID/正文的 duplicate；
批次身份由原异常与窗口核对；观测表本身没有批次列。改规则、缺测或存储证据
变化保持阻断，不覆盖已冻结候选。原异常未确认时
返回等待，不抢发未知关联恢复。

此函数由调用方**明确调用**，没有后台扫描器、正式迁移、真实 B 规则 API
调用或 HTTP 投递，也不将 B 告警自动设为 resolved。正式接线须由 C 在
已合并 B01 后提交唯一 C 迁移，A 协调调度入口；恢复 outbox 的实际 B 接受
及原告警 resolved 必须按来源回读确认，失败时保留同 ID 重试证据。
本模块合成 B 端口测试已覆盖：恢复候选首次被接受但原告警仍 open 时，
本地 outbox 留 pending/retry；下次按同 source ID 回读原告警 resolved 后
才标记 delivered，过程中只模拟一次发送。该测试没有联系真实 B 服务。

## 2026-10-19 恢复进度隔离持久端口（待正式迁移和业务接线）

`backend/app/modules/telemetry/recovery_progress_candidate.py` 在独立候选
metadata 中保存原异常 ID、可信设备/仓/批、原采样时刻、**冻结的原规则条件**、
连续观测 ID、时间水位及版本号。每步事务使用版本比较更新；再次打开测试库后
仍可继续累计，重复/迟到/冲突/缺测不会凑足恢复条件，具备资格后证据不再变化。
调用方必须先核对原异常与 B 的历史规则，并从 C 已持久观测生成样本；此端口
本身不查询 B、不创建表、不自动投递或改变告警状态。测试只在临时 SQLite
文件使用 `metadata.create_all`；正式 C Alembic 必须接审核合入的 B01 唯一
head，且还需把窗口与恢复 outbox 的生成纳入可恢复事务。当前不能把本端口
称为真实 E1 恢复或 PostgreSQL 重启验收。

## 2026-10-19 原规则恢复条件绑定（独立 C 端口）

`recovery_rule_candidate.py::bind_original_recovery_rule(original, selected)`
消费原已冻结 F03 温度高异常正文，以及 B 在**原异常采样时刻**选出的历史
`RuleSelectionEntry.model_dump(mode="json")`。它逐项核对仓/批/指标、原快照
`snapshot_id`、`rule_version`、severity、原异常阈值及生效半开区间；后来版本
即使阈值相同也不能替换原版本。B 规则没有明确 `recovery.comparison` 与
`recovery.threshold` 时返回无自动恢复条件，不猜测异常阈值。B 可配置 1 条
或 2 条，但 C 按固定 S0 最小策略提升到 3 条；B 更严格的样本数仍保留。
B 当前 DTO 没有间隔字段，C 使用 S0 的 90 秒；若后续历史规则明确给更短
间隔，端口可保留，不接受超过 90 秒。此端口只生成纯判据，不持久保存窗口、
不发送恢复、不把告警状态标为 resolved。目前仅支持 C 已实现的
temperature_high 路径；低温
恢复须另补明确比较符、单测和真实接线。规则的旧版本查询由 B 提供，A 不应
让页面或调度器把“当前最新规则”传给旧异常。

A 已在 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6265029865)
按 A c15+B3f+Cdf232 精确组合复测真实 PG/HTTP **33/33**，旧摘要失败解除；
此探针用候选 C DDL，非正式迁移或完整 E1。B 新 `b01_alerts_storage` 在
[PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) 的
8561 头仅为未合并草稿；C 对该头测试入口的[修改请求](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5572604584)
要求 B 步测试允许合法 C 后继且 PG 写测试只触及专属测试库。正式 C 迁移仍
接 B 审核后合入 main 的唯一 head，不从 PR 分支创建平行正式版本。

## 2026-10-19 恢复窗口隔离判据（本机候选，未接线）

`recovery_window_candidate.py` 只接受原异常规则中**明确配置**的恢复比较符、
阈值、样本数和间隔。调用方须从原不可变规则快照取得这些值并持久保存窗口，
不能把异常阈值推测成恢复阈值。样本数至少 3，间隔上限最多 90 秒；
原规则可要求更多样本或更短间隔，不可放宽该最小策略。按接收顺序，
只计同设备/仓/批、原异常之后、
采样时刻严格递增、质量精确为 `ok`、不同观测 ID 的样本；重复不计数，
迟到不回填，冲突/缺测/坏值/再异常重置，间隔过长从当前有效样本重新起算。
达到条件后冻结观测 ID 集合，供后续持久候选事务使用；**它不会写数据库、
调用 B、自动关闭告警或改变页面风险**。规则未给恢复阈值时调用方不得运行
该判据。正式持久窗口、B 原规则读取和自动调度仍待唯一迁移与共享接线。

本地 F03 摘要/恢复准入已按 B `3f43af70a29bfc0859fed711333cd38e7e505a05`
准备；C 对该 B 头的精确代码 review 见 PR #10。下面若引用更早 B/C 头，
仅是历史阶段记录，不能覆盖此版本事实。

## 2026-10-19 C→B 恢复回读接线

PR #11 的独立 C HTTP 适配现在消费 B PR #10 `9cf9a5de55d48bf0761655b6f2cda844ec5975f6`
的 `GET /api/v1/candidates/by-source/{source_event_id}` 中 `content_digest`
和告警生命周期 `status`。C 对恢复候选即使收到 201/200、相同来源和正文摘要，
仍要回读**原异常**的 `status=resolved` 才将本地恢复 outbox 标为 delivered；
回读超时、拒绝、状态仍 open/acknowledged 或状态不可验证时保留同一 ID/冻结正文
有界重试，不把业务恢复说成已完成。C 本机通过合成 HTTP/端口测试；B 实际路由
未挂公共根，尚无跨模块实跑。B 请维持恢复写入和原告警 resolved 的同事务语义、
by-source 返回可核验的 `source_event_id`、`content_digest` 和 `status`。A 后续
只需按签收包挂一次 B/C 相对 router，勿重复 `/api/v1`。正式迁移和任务调度仍未接。
若冻结正文与当前 F03 DTO 不兼容，C 仅做 by-source 回查；查无后在本地把任务
标记 `rejected/invalid_local_candidate` 并保留原 ID/正文，不向 B 发错包，
也不在租约到期后无限重试。正式运维需要人工可见此状态并处理版本兼容，
不得批量改写已冻结事件以便“通过”。

## 2026-10-18 鉴权 HTTP 候选接线

`backend/app/modules/telemetry/http_candidate.py` 新增相对 router
`/telemetry/observations`，列表、详情和固定快照 CSV 均通过现有 `CurrentUser`
核验活跃账号，不自行添加 `/api/v1`。列表首次创建成员/投影固定的 900 秒
snapshot；后续分页用同 actor/筛选/策略绑定的签名游标，CSV 使用同 snapshot。
详情提供 exact raw 与每次接收结果。快照每 actor 最多 4 个、全局最多 64 个
有效项，满额 429 `snapshot_capacity` + `Retry-After: 30`；跨人/筛选不符/失效
422，存储异常 503。当前首版没有逐仓 ACL，任何活跃账号可读取全库范围，
与 S0 签收包一致，页面和材料需如实说明。

**此 router 未挂共享根路由**，只基于 C 独立 candidate metadata 和测试库。
集成 Agent 在 B 唯一迁移正式合入、C 迁移审查后，需将候选表接正式 schema，
再在 `backend/app/api/main.py` 挂一次相对 router；绝不可在生产启动调用
`metadata.create_all`。当前没有正式 app DB/PG HTTP 测试、生产游标签名密钥轮换、
页面/导航或 E1。测试中的真实 JWT、快照容量、跨人/筛选拒绝、CSV 同集合，
只证明可执行的 HTTP 适配边界；本机无 Python3.14/FastAPI 环境，远端 CI
和正式迁移需分别记录。

`test_telemetry_storage_postgres_candidate.py` 另加真实 PostgreSQL 下的模块
HTTP/JWT/CSV/故障测试，仍沿既有 `FRESHLINK_C_TEST_POSTGRES=1` 和
`test-db/freshlink_test/FreshLink-test` 精确守卫，在随机 C schema 内建测试用户
表并清理。默认本机无此环境会明确 skip；只有实际隔离 PG job 跑到该新用例
才可称它通过，旧 2/2 证据不能代替新用例。

状态：接线提案，未实施。请指定集成 Agent 协调；C 不直接改根 API、公共导航、Compose、锁文件或迁移链。下文 2026-10-11 至 2026-10-13 的旧私有库 URL、PR 编号和 SHA 仅是历史证据；**当前协作、评论及代码交付均使用新公开仓库**。

## 2026-10-17 C 出站队列隔离候选

`backend/app/modules/telemetry/outbox_candidate.py` 已在 C 自有隔离 metadata 中
试验固定 `source_event_id`/不可变正文、重复与改正文冲突留证、持久租约认领、
超时/可重试回队和永久拒绝。它需要调用方提供已持久的观测 ID 和事件 ID，
本身不生成业务候选，也不调用 B；本机临时 SQLite 重开库测试不是正式 PG
worker、真实 by-source 查询或 E1。B 同版 F03/RULE 签收后，C 须把 B 回执
与超时查源映射到此状态机，并给租约并发/重试次数上限/错误分类、生产配置
和拒绝证据保留上限；正式迁移仍接唯一已合并 head，不复制测试 `create_all`。

## 2026-10-16 当前接线快照

- 当前共同 main `fb6de8408c860b277471cdf1697b72fd34162f38`；C 分支 `feature/c-data-quality-bootstrap` 的实时版本以 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 为准，保持 draft。前一功能提交 `e3abb700174f437255bb54f76d07f52dc5c38126` 已普通推送；下方按旧日期记载的 main/HEAD 和未上传状态只供历史追溯。
- C 的 `storage_candidate.py` 在**隔离候选 metadata** 中写 observation/reception/evaluation、持久质量 tag，以及复用同一 `reception_id` 但原文改变时的独立拒绝审计；`history_candidate.py` 做持久质量/时间筛选，`snapshot_candidate.py` 固定有序成员与投影并渲染同快照 14 列 CSV，`snapshot_cursor_candidate.py` 生成内部作用域签名续页标记。候选表仅供独立测试，不能在生产 `create_all`。传入 actor UUID 没有登录信任来源；签名密钥只由调用方注入，尚无正式权限、密钥轮换、路由、C 迁移、PG 快照并发实跑或页面。过期快照隔离清理已做有界单机测试，生产读写锁定与容量调度仍待。
- A/集成方：待 B 正式迁移接唯一已合并 head 后，协调 C 正式表/迁移与可信 A ingress 的一次调用、成功/重复/冲突/临时故障回执、受权用户/权限政策、根路由和前端导航。A 入口须给出拒绝审计/重复恶意接收 ID 的大小、数量和保留期限制；C 候选拒绝尝试按所声称 ID+摘要去重，但不同恶意原文仍会占存储。
- B：PR #10 仍在第三轮复审后的 `1e5e005970e5cdeca7fcf6a4210e0857787f85b1`，F02 批次临时读取故障、有界后继到期后旧规则复活、severity 三值共三项待修；正式 F03-RC2/数据恢复引用、唯一迁移 head 尚未共同冻结。C 不把固定候选 JSON 发送到旧 DTO，不凭当前 SQLite/CI 宣称 B 告警或 E1 已接通。共享问题详见 [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5)。

## F03-RC2 四项 C 答复（2026-10-15）

见[共同 #5 C 答复](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6194380612)：候选 `severity` 只复制有效规则的 `info|warning|critical`；C 负责原始观测、质量、阈值和 evidence 一致性，B 仅需有界 JSON 透传、全文摘要与回读；A 的可信 `received_at` 留在 C 接收证据，B 候选不接收；缺测、坏单位/时间、无规则、重复和冲突不投递。暂时性的规则读取故障必须保留待评估任务重试，不误作永久 `unknown_batch`。C 的固定候选投影新增枚举校验，但这不代表正式 F03-RC2 签收或真实投递；A 的 PR #10 复审和 B 的同版 DTO/F02 故障分类仍待确认。

## 2026-10-15 最新依赖与 C1 候选

- 共同 main 仍 `f7fe62a95eceee75bebba3e135fc67d6c7728643`。B 新库 PR #10 现已推至 `1e5e005970e5cdeca7fcf6a4210e0857787f85b1`，分支包含该 main；B 在 #8 记录同版 F03-RC2 DTO、按源回读、故障边界修复及该 HEAD CI 成功。下方“B 仍只有较小 DTO / 未上传”是旧版本快照，不再作为当前事实。正式同版签收、B 唯一迁移 head 合入、C 对 B 实际 API/PG 验证仍待完成。
- C 新增 `storage_candidate.py::ingest_observation(engine, envelope)`：输入只消费 A 可信封装，保留 exact 原文与可信接收元数据，在 C 独立候选表的一次 SQLAlchemy 事务中记录 observation、reception 与 evaluation work；同设备/消息去重、同接收重试、冲突和数据库失败有显式结果。非法指标形状只保留 `unknown/held_unknown`，不能排队生成阈值候选。**它没有正式 Alembic 迁移或应用路由，不能对生产 engine 建表或称已接 A→C 正式入库。**临时 SQLite 文件数据库和 8 项事务回归只证明隔离实现；PostgreSQL、并发、完整重启、B 规则和候选投递仍待实测。
- 集成方后续需在唯一已合并 A01→B head 上协调 C 正式表/迁移及可信 A ingress 调用，明确失败回执和拒绝证据；C 在同版 B 规则/候选契约签收后补历史规则选择与 outbox。共享根路由、权限和 Compose 仍由 A 协调。

## 新仓库当前接线核对（2026-10-15）

- 当前共同 main `f7fe62a95eceee75bebba3e135fc67d6c7728643`；C 自有分支 `feature/c-data-quality-bootstrap` / [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) HEAD `e0f14c44c19597c925ca5e3ed387d8f01d1796cd`。迁移入口仍为 `WAIT_VERIFICATION`；S0 [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5)、E1 [#6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6) 均未完成。
- **A → C**：main 已有 `iot/ingress.py::receive_telemetry`，返回 `ReceptionEnvelope(raw_text, authenticated_device_id, warehouse_id, received_at, reception_id)`；它声明只能由可信设备适配器在认证、ACL 和绑定核验后调用。C 的 `envelope_preview.py::prepare_receptions` 只在固定对象上保留 exact 原文、分离可信接收时间并计算质量；不是正式 MQTT 消费或持久接收。设备自报的 `received_at` 不覆盖 A 的可信时间；C 的预览仅接受 UTC 采样时刻且拒绝无声截断更高精度。生产精度、无效数据回执、拒绝证据与重试签名仍待 F01 同版确认。
- **B → C / C → B**：[PR #14](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/14) HEAD `99c52511f8c8bf9deedbd617ed4c09a780c1fbdf` 给出 F03-RC2 单批候选目标；C 已在 [#5 的字段级答复](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6181720375) 接受目标并列出失败/重放语义。B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) 远端 HEAD `f9b85638819e60d665ca9c188da8a829ed57fda1` 仍是较小 DTO，未提供可验的同版完整接收、by-source 回读或已合并唯一迁移 head。C 当前 `candidate_preview.py` 只用合成规则和外部分配 ID 输出固定 JSON；无真实规则选择、持久 outbox 或投递。B 发布同版实现后，再核对候选不可变正文摘要、批次归属、`timeout → by-source 404 → 同 ID 重试`、永久拒绝与数据库暂时故障。
- **C → 查询/页面**：现有 `query_preview.py` / `csv_preview.py` 只对内存样例做筛选、排序与 14 列人工查看预览；无鉴权、PostgreSQL 查询、正式 HTTP、签名快照或页面。F06 的固定服务端快照与页面/CSV 同 actor、同筛选、同成员仍是待签收目标。A 集成时仅在 `backend/app/api/main.py` 挂一次 C 相对路由；C 在 B 迁移并入当前唯一 head 后提供自身表、服务、路由与权限测试。

以上版本只描述本次核对的远端事实，后续开工须重读新 #5/#9、PR #10/#11/#14 和 main；不能用本节 SHA 代替实时检查。

1. A → C：确认已认证合法观测的生产位置、schema_version、UUID 来源、UTC 时间/单位、重发时的幂等内容边界，以及设备与仓库关联失败语义。当前纯函数仅处理 JSON 样例，不接受 MQTT。
2. B → C：确认规则/批次只读快照签名、版本与有效时间、阈值比较和恢复语义；无规则保持 unknown。
3. C → B：确认候选事件稳定 ID、observation_ids、evidence、接收/重试/永久失败边界。当前 candidate 只是局部计算，未持久化或发送。
4. C → 页面：待共同契约确定后申请数据页面路由、导航入口和根 API 注册；查询/分页/CSV 需要统一筛选与鉴权。
5. 迁移顺序：等 A01 合并及 B 最小迁移合并后，C 再基于当时已合并 head 建正式观测迁移。不得另起迁移链或清库。

6. 共享 CI 权限问题：PR #11 的 Playwright Tests 工作流在 dorny/paths-filter 读取 PR 文件列表时以 Resource not accessible by integration 失败（运行 https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38060433663）。请工作流维护者核对 pull-requests: read 等所需权限及现有工作流配置；C 不修改 .github/workflows。此失败不等于 C 数据处理单测失败。

## 本轮需三方明确回答（2026-10-11）

- **A，关联 #7/#5**：合法遥测由哪一函数/队列交给 C；设备鉴权和仓库归属失败如何返回；`schema_version` 允许值；`message_id` 的生成与重发保证；`received_at` 由谁记录；metrics 顺序是否有语义。请按 `examples/telemetry-contract-proposal.json` 的首条、重发和冲突三例确认。
- **B，关联 #8/#5**：按 `sampled_at` 获取规则快照的签名、批次关联与 `rule_version`；温度相等时是否触发及恢复条件；无规则/批次冲突的失败状态；按 `source_event_id` 接收候选后已存在、可重试、永久拒绝和超时的返回。C 不把样例候选称作已入库告警。
- **集成 Agent，关联 #5**：查询与 CSV 是否共享样例中的完全相同筛选及鉴权；时间右边界、稳定游标、导出上限和列顺序。确认后才申请根路由/导航接线。

## A RC1 对接待定项（2026-10-11）

A 在 PR #4 的 `docs/modules/A/01-contract-proposal.md` 建议 `iot/ingestion.py` 完成 MQTT topic、设备身份和有效仓库绑定后，调用 C `telemetry/service.py::ingest_observation(envelope)`。C 原则接受该方向，但两处服务均未实现；请 A 给出 envelope 的 raw/认证设备/绑定仓库/received_at/重发字段及失败审计责任、reason_code 枚举和输入上限。C 的生产存储由 C 事务生成 observation_id、保留各次 raw/reception 证据；必须等 A01→B 迁移进入当前 main 后接线。

C 当前新增 `query_preview.py::select_preview_observations(results, filters)` 仅是已处理样例的纯筛选，不挂根路由。A 的采样/接收双时间窗、NULLS LAST、游标水位、权限、CSV 上限/列序建议需与 B 仓库 ACL 和三方契约统一后接真实查询。CSV 人工表格与机器导入用途及公式文本保护规则请集成方确认；详情见 `01-contract-proposal.md`。A 的 RC1 新版四条观测在 `examples/telemetry-rc1-proposal.json`，不替代历史 `draft-1` fixture。

## B 新答复后的具体接线缺口（2026-10-11）

B 的 [#5 答复](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6108448780) 仍为提案，且 B 分支 `447ac38a48a853d83b0afe950b11a945b170ff36` 尚无正式规则服务/迁移。C 的逐项接受与替代详见 `01-contract-proposal.md`。@xixidamei 请以可消费的只读签名及 JSON 回答：

- `get_batch_rule_snapshot(warehouse_id, batch_ids, sampled_at, metric_code)` 的固定返回形状、历史版本、空规则与多批次/重叠/权限错误；版本必须在候选中可稳定引用。
- 按 UTC 带时区的半开有效区间选版本；修正 append-only 与修改旧版 `is_current/superseded_by` 的矛盾，明确历史候选不随 current 变化。
- 规则快照的比较符号、单位、候选类型/等级、恢复持续条件，以及温度和湿度指标在 B 的类型映射中如何保留。
- `receive_risk_candidate` 的精确入参/结果、同键重发与冲突证据、超时按键查询、拒绝与可重试错误。C 管候选持久 outbox/投递重试，B 管事件事务幂等及通知重试；两者需分别验收。
- 恢复若作为新事件，给新幂等键及原异常事件引用；缺测、缺规则不能自动恢复。B 的 `raw_payload` 应明确用途、字段、大小与脱敏规则。

共享集成方请核对实际根 API 接入文件（当前读到 `backend/app/api/main.py`）和单 Alembic head；B #5 评论里的 `backend/app/main.py` 与“两条新链”不可直接接入。A01 #4 已合并至共同 main `a99c2a091204012891b8fa0c80e10b63087c57c1`；B #10 尚未合并，C 不创建第二条正式迁移链。

## A 新接收封装和导出提议的接线点（2026-10-11）

A 的 [新版补答](https://github.com/Cynthia060913/freshlink-zhikong/blob/caf8e3a6273737623873be60efca9651a5f4fc9e/docs/modules/A/01-contract-proposal.md) 已给出内部 envelope、资源上限、reason_code、游标密钥和人工查看 CSV 用途；C 的接受/待定项见 `01-contract-proposal.md`。PR #14 的实际 A 边界是 `iot/ingress.py::receive_telemetry`，可信适配仍待接线；设备原包可自报 `received_at`，但只作原文证据，不覆盖封装中的可信时间；C 持久保存 exact raw_text 与每次 reception，另对可信元数据处理。当前固定纯函数输入包含服务时间，仅是处理样例。

A 的 MQTT 身份/绑定/超限拒绝审计与待发队列由 A 实现并测试；C 返回持久成功/重复/冲突/永久无效/临时故障，A 只在已确认落库后清队列。C 的原始观测、reception 与候选 outbox 正式表需等 A01→B 单 head 与版本化契约。查询、计数、CSV 和页面须用同一权限/筛选；A 提议首版无逐仓 ACL，B 确认后再固定。生产签名游标与现有纯预览标记不是同一接口。

## A→C 封装纯处理投影（2026-10-12；PR #11）

C 新增 `backend/app/modules/telemetry/envelope_preview.py::prepare_receptions`，结构性读取 A 的 RC1 接收封装，不 import 尚未合并的 PR #14。它把可信仓库与 UTC 接收时间只加入质量处理视图，单独保留 exact `raw_text`、原 `raw_payload` 和可信接收元数据。设备自报 `received_at` 不进入规范时间；原文缺仓库不会被误判为 C 缺字段。身份与绑定不符显式失败；正常、高温、缺失/坏单位、批内重复/冲突与无规则样例有 C 单元测试。

2026-10-15 补充：该纯投影现额外验证 `raw_text` 可严格解析成对象且与 `raw_payload` 的 JSON 类型和值一致；重复成员、非 JSON 常量或两者不符显式失败，防止原文证据与清洗结果不一致。这只是 C 边界防护，不表示 A 的正式适配、持久拒绝审计或 HTTP/MQTT 接线完成。

本函数只是可执行的投影原型：`batch_duplicate` 只在一次调用内有效，`threshold_c` 仍是固定测试规则；没有数据库事务、跨重启幂等、真实规则快照、持久候选、MQTT 监听、授权查询或 CSV。正式接线仍由 A 提供可信身份/封装，B 提供规则及仓库政策，C 在已合并 B 唯一迁移 head 上实现观测/reception/outbox 持久化。

## C1 CSV 预览接线边界（2026-10-12）

C 的 `csv_preview.py::render_preview_csv` 对固定内存结果复用 `query_preview.py::select_preview_observations`；2026-10-12 已把样例 CSV 收敛为 A 提案的有界 14 列，不再把完整 raw JSON 放入导出。exact raw 仍由受权详情/接收证据提供。本函数不接受 actor，不能挂公开路由。集成方需在 F06 确认正式列与授权后，接真实数据库筛选和接收水位，并让页面/CSV 共享 actor 权限、过滤条件和固定水位；签名游标与 HTTP 下载、错误码属于正式服务接线。无数据需在页面显示“无数据”，CSV 仅表头；超出 10000 行拒绝。此预览只能证明固定样例字节格式与筛选一致，不能作为权限、重启或大数据集验收。

## A PR #14 第 7 节的新目标与待集成（2026-10-12）

C 已把 F01 合法 JSON 指标上限与未知风险、F06 14 列全角/半角危险文本人工查看预览补到 PR #11，详见 `01-contract-proposal.md` 最新节。正式 `ingest_observation` 仍需在 C 事务中返回 `invalid`/原因并存有界拒绝证据，不能把纯函数 risk unknown 冒充持久失败结果。

A 集成方可在确认 B 权限后，于 `backend/app/api/main.py` 挂 C 相对前缀 `/telemetry/observations` 一次；`/api/v1` 由主应用统一加。目标路由为列表 `/`、静态 `/export.csv`、动态 `/{observation_id}`；C 实现路由时先注册静态导出。查询/详情/计数/导出共用 CurrentUser、规范筛选与权限。请 A/B 在 #5 核定首版仓库权限和接收水位是否必须保证跨请求固定结果集；若必须，需快照标识与并发写入验收，不可仅依赖 UTC 水位。正式迁移仍等 B 唯一 head 合并。

## B 目标与 A 固定快照的共享接线请求（2026-10-12）

B 在 [S0 答复](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6127332268) 接受首版活跃登录读/管理员写、无逐仓 ACL，确认历史规则与五类候选回执目标；B3 服务/正式迁移尚未实现。C 可以按其 `select_rules_for_observation` 单一 envelope 准备消费，但请 B 给多批/重叠失败 JSON、候选携带 `rule_snapshot_id`，并明确 C 投递超时后 `get_candidate_by_source` 查无时重试同 ID、查有时如何核对内容。正式接线等 B 合并唯一迁移 head。

A [PR #17 第 8 节](https://github.com/Cynthia060913/freshlink-zhikong/blob/63fee5cb87587208877b31f46c7d694fb9c4979d/docs/modules/A/01-contract-proposal.md) 改 F06 为服务端固定结果快照。C 消费方接受该目标，新增 `snapshot_id` 到 page、游标与 CSV；仅 `received_watermark` 不再作为固定集合保证。建议 900 秒 TTL、每 actor 4 / 全局 64 活跃快照上限，满额 429 `snapshot_capacity` + `Retry-After:30`，待 A/B 确认。A 仍只在根 API 挂 C 相对 router；C 自有快照存储、签名游标和资源清理，正式 C 表/迁移待 B 头合并。非空/失败 JSON 在 `examples/query-snapshot-rc1-proposal.json`，当前无真实数据库快照。

## B F03 接收 DTO 与共同候选目标的差异（2026-10-13）

B PR #10 HEAD `58546e9993381b92d158cb437a31d61b95dfb1c1` 的
`backend/app/modules/alerts/models/event.py::CandidateCreate` 目前只声明
`source_event_id, kind, batch_id, metric_code, sampled_at, value, unit`。
共同 main `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813` 的
`contracts/INTERFACES.md` F03 目标则含 `observation_ids, warehouse_id,
batch_ids, occurred_at, severity, evidence, algorithm_version`；C 候选提案再带
`rule_version, rule_snapshot_id`。C 样例原样不能作为当前 B DTO 的有效请求；
压缩成最小 B DTO 会缺少共同目标的观测/规则追溯字段，且 B 的规范摘要取 DTO
`model_dump`，不能假定额外字段进入摘要和 `payload_json`。

已把 [具体问题与两种解决方向](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6148596048)
直接写给 A/B，并在 [PR #10](https://github.com/Cynthia060913/freshlink-zhikong/pull/10#issuecomment-6148602066)
指出读阶段数据库故障尚未显式映射五类回执。建议 B 扩展版本化 DTO，保持
`source_event_id` 的同内容摘要幂等，并给有效/失败 JSON、持久查询后追溯
`observation_ids/rule_snapshot_id` 的契约测试；如选择最小 DTO，需先更新
F03 公共约定和跨服务可追溯关联的验收路径。此处是静态核查，**未运行 B
服务、数据库或 E1**。在确认前，C 保留丰富候选为目标提案，不投递当前 B
路由；只把固定回执样例的显式 `invalid/unknown_batch` 与可重试
`storage_unavailable` 原因按 B 现有源码对齐。

B 的正式迁移仍未合入 main；C 不另起迁移链。A/B 确认 F03 后，C 才实现
对应持久 outbox/投递和真实跨服务契约测试。

## 时间精度的 C 纯处理边界（2026-10-15，待 F01 确认）

Python `datetime` 只保留六位小数。旧纯处理若接受七位及以上的秒小数，
`sampled_at` 会被静默截成微秒，同一设备/消息 ID 下两个不同采样时刻可能被
误判为同内容重发。C 纯处理现将超过六位的采样或接收时间标为
`invalid_time`，保留 exact raw、风险保持 unknown；同键原文变更仍判 conflict。
六位以内仍接受 UTC `Z`/`+00:00`。这是 C 预览的保守边界，**不是已冻结的
生产 F01 格式**；请 A 确认设备采样时间最高精度，正式接收服务不得无声截断
更高精度时间。没有据此宣称数据库、MQTT 或 E1 已验证。

## 新公开仓库 F03-RC2 单批候选消费提案（2026-10-15，未接线）

对照新仓库 PR #14 `contracts/F03-RC2.md` 和
`contracts/examples/candidate-f03-rc2.json`，C 新增
`examples/candidate-f03-rc2-c-proposal.json` 固定向量。它从 C 的独立
`schema_version="1.0"` 高温**处理视图**与显式批次/规则样例核对：顶层使用
`batch_id`、`sampled_at`、`value`、`unit`、`metric_code`，同时保留
`source_event_id`、单个 `observation_ids`、`warehouse_id`、
`rule_snapshot_id`、`rule_version`、`algorithm_version` 和质量/阈值证据。
批次与仓库必须对应，规则以有时区 UTC 采样时刻选择；同一观测若作用于不同
批次，各候选需要不同且持久的来源 ID。样例里的 `received_at` 仅属于 C
处理视图，生产时由 A 的可信封装给出，不从设备原文信任。

缺温、错单位、无规则、重复或冲突均不生成正常/低风险或第二个候选；有效
高温仍保留原始值。C 的目标是在同一事务中保存候选及不可变正文，后续
超时查无时使用**原 `source_event_id` 与原正文**有界重试；B 回重复需核
规范内容，永久拒绝/冲突留失败证据，不让通知服务内部重试代替 C→B 投递。
当前 JSON 与单元测试只验证固定映射和不发条件，没有 B 接收、数据库 outbox、
跨重启 ID 稳定性或恢复事件。

请 A/B 在 S0 #5 对同一 F03-RC2 提交确认：B 的候选 DTO/规范摘要与 by-source
回读须覆盖完整证据；`timeout → by-source 404 → 同 ID 重试` 不可转永久拒绝；
恢复事件的新来源 ID 如何引用原异常，及 B 的历史规则选择接口、批次归属
校验和数据库失败分类。新库远端 PR #10 当前尚非 RC2 实现，C 不向其最小
DTO 投递此样例，也不擅自改共同 contracts 或迁移链。

2026-10-15 C 内部补充：`backend/app/modules/telemetry/candidate_preview.py::build_candidate_preview(processed, *, observation_id, source_event_id, rule)`
现将固定高温处理结果与合成历史规则投影成上述 RC2 JSON。观测和来源 ID 均须由未来的持久事务提供；函数不创建 ID、不读 B 规则、不发送事件。它对缺规则或非候选返回空结果，对规则归属、采样时刻有效区间、单位、阈值或 ID 不一致显式拒绝。该纯投影只让样例映射可执行，**不构成 F03 正式签收或 C→B 实际交付**；B PR #10 同版 DTO/摘要及唯一迁移 head 仍是正式接线前置条件。

## F03-RC2 数据异常恢复的 C 提案（2026-10-16，待 A/B 确认）

`examples/recovery-f03-rc2-c-proposal.json` 给出合成高温
`77777777-7777-4777-8777-777777777777` 与三次有效回落后的**新**恢复来源 ID
`99999999-9999-4999-8999-999999999999`。恢复候选通过
`related_source_event_id` 引用原高温，且原始高温观测、原异常事件均不删除。
同一恢复 ID 与同一不可变正文重送只能去重；改正文应冲突，不能再关闭一次。
示例中的 `<=8 °C`、连续 3 次、原异常规则快照与恢复 severity 均为待确认
规则样例，并非 C 已实现的生产判定。`received_at` 属 A→C 可信接收记录，
不进入 C→B 候选。

目前 B PR #10 `CandidateKind` 尚无 `temperature_recovered`，`CandidateCreate`
尚无 `related_source_event_id`。A 的 `DeviceEventCreate(kind=recovered)` 是设备
生命周期事件，不能替代 C 的温度恢复；若向当前 B DTO 发送新增字段，可能被
忽略而不参与正文摘要。**C 不发送本提案到当前 B 端点。** 请 B 在 F03 同版
DTO/摘要/持久行/by-source 回读中增加恢复类型与关联字段，并说明由哪层将
原告警从 open/acknowledged 变为 resolved、重复恢复如何保持一次状态转换。

待共同决定：恢复规则用原异常快照还是恢复采样时刻的新快照；乱序晚到样本
何时封窗及是否允许补证；中间缺测/无效/冲突/重复消息是否重置连续计数。
在决定前 C 的安全默认是**不生成恢复**，保持原异常可追溯、当前风险未知，
不把缺测或无规则显示为 normal。原异常未送达时，C outbox 应先交原事件，
恢复留待投递；超时且 by-source 未找到时用同一恢复 ID/正文有界重试。
永久拒绝/内容冲突留失败证据，不能静默丢弃。

## F06 独立遥测页面候选（2026-10-18，尚未接公共导航）

`frontend/src/features/telemetry/TelemetryPage.tsx` 和同目录 `api.ts` 使用现有
客户端的 bearer 配置调用 C 相对路由预期的
`/api/v1/telemetry/observations/`、`/{observation_id}`、`/export.csv`。
采样与接收时间筛选表单中的 `datetime-local` 明确按 UTC 输入，转换为带 `Z` 的时间；
筛选提交或刷新会请求新快照。分页携带同一快照及签名游标，CSV 携带同一
快照 ID 与已提交筛选，不采用尚未提交的表单值。页面显示缺失/未知与错误，
不将空结果或服务失败解释为正常。

此组件未接公共路由/导航，当前主应用不会显示；A 集成时应在正式 C 迁移和
HTTP 服务获审后挂载同一相对后端 router，并在共享前端路由增加页面入口。
还需真实浏览器复核登录、筛选、翻页、详情、CSV 文件行数/列/权限及失败
展示。本候选代码和普通前端检查不等于这些场景已通过。

F02 候选评估写入的 `risk.rule_version` 由 C 历史投影带入后续新快照的
页面列表及 14 列 CSV；已创建的旧快照仍保留先前 `unknown` 和空规则版本，
不悄悄覆盖。此一致性仅在隔离 SQLite 快照/CSV 测试通过，正式应用/PG
与真实浏览器尚未验证。

## A 持久接收队列到 C 入库的待接线端口（2026-10-19）

已只读核对 A [PR #26 当前源码候选](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/26)
的 `telemetry_inbox.py` 和 `telemetry_worker.py`：A 的
`TelemetryDeliveryWorker(engine, sink: StoreSink)` 将可信
`ReceptionEnvelope` 在 A 租约事务外传给 `sink`，只有取得匹配
`reception_id` 的 C 持久回执后才清理 A 原文队列。C 已有
`ingest_observation(c_engine, envelope) -> IngestResult`，回执字段
`status/reception_id/observation_id` 与 A `ReceiptLike` 形状相符；
同一 A 租约重试应沿用原 `reception_id`，C 对它幂等并保留 raw。

集成方在 A、B、C 唯一迁移链和真实批次绑定获审后，可注入仅调用
`ingest_observation` 的 C 存储端口；须给数据库查询和网络操作设置有界超时，
不能在 MQTT 回调中同步跑 C，也不能把 A 的 QoS1 ACK 当作 C 已入库。
A `invalid/conflict` 终态在 C 留原始拒绝证据，A 留元数据回执；C 暂时库
失败保持 A 原包及原 ID 重试，C 已提交而 A 写回执失败时重放不得产生
第二条观测。A PR #26 尚未合 main，C 没有在当前 PR #11 代码上实际
运行 A 队列→C PG 组合；A 早期在 #6 的分段证据按其精确旧 SHA 引用，
不自动视为本头 E1。A/B/C 正式接线后应在 #6 记录同一版本、原包/观测
ID、重建及失败恢复的实际结果。

## C→B 持久候选投递协调层（2026-10-18，独立端口）

`backend/app/modules/telemetry/delivery_candidate.py` 消费 C outbox 的不可变
`source_event_id`、JSON 正文及 SHA-256，先按来源 ID 查询 B，再发送。发送超时后
立即按同一来源 ID 回查；确认 B 已接收且正文摘要一致才标记 `delivered`。
无法确认时保持同一来源 ID 和正文，按 30 秒起、有上限的退避重试；无效或冲突
保留拒绝记录，重试预算耗尽也留失败记录。旧租约不能覆盖新处理者的结果。

这只是 C 的可注入传输端口及 SQLite 文件数据库测试，**没有生产 B HTTP
适配器、调度器、共享路由或正式迁移，也没有真实 B 服务送达证据**。正式接线
需要 B 同版接口支持按 `source_event_id` 回读、返回其持久正文摘要以及区分
新接收、同文去重、内容冲突、无效和可重试故障；A/B 确认摘要的规范化算法与
HTTP 错误映射后再实现适配器。无法核对来源 ID 或正文摘要的回复不能作为送达。
后续独立补充有界到期扫描和单批处理：仅返回到期 pending 或租约已过期的
来源 ID，每条仍须原子抢租；已送达、拒绝和未到期项不会重新发送。调度启动、
监控与正式数据库索引仍须在真实服务/迁移获审后接入。

B 在 S0 #5 的 2026-10-18 回复给出固定 `CandidateCreate` 规范化样例及
`content_digest=sha256:20789a39ec33eba8c25651c3f41c5c077c466c852ff58a11532cb211ca5aa234`。
C 的固定正文以同样的键排序、紧凑 JSON、UTF-8/非 ASCII 原文计算，新增单测
逐字对拍此摘要。此对拍仅覆盖该已规范化向量；真实适配器发送前仍须确保
UUID、UTC、数值、severity 和全部可空字段与 B `CandidateCreate` 规范化结果
一致，否则 B 的摘要可能不同，C 不能把回复误判为 delivered。此段是
7b1cf26 时的版本记录；恢复候选增量的更新见下节。实际跨模块投递仍待验证。

### 2026-10-19 B 兼容摘要约定与 C 对拍

B PR #10 的 `3f43af70a29bfc0859fed711333cd38e7e505a05` 已把
schema 1.0 非恢复候选的空 `related_source_event_id` 从**摘要字段集**精确
排除；HTTP 正文仍可带该字段的 `null`。因此普通高温固定向量继续是
`sha256:20789a39ec33eba8c25651c3f41c5c077c466c852ff58a11532cb211ca5aa234`，
既有 B 行的同 ID 同文重放仍可返回 `accepted_duplicate`。恢复候选的非空
引用仍参与摘要，固定向量 `sha256:d6d1ef23731e60bdcdee94f36c7b271952d071ce7d4d53841cb4dd7cee301c0c`
不变。C 的 F03 staging 保存完整规范化 HTTP 正文，同时以 B 的精确摘要字段
集写入 outbox `body_sha256`；恢复后重开及同 ID 同文重复均在隔离测试中核验。
恢复候选在本地 staging 前先读取自身 outbox 中不可变的原异常正文；只有原
`temperature_high` / `temperature_low` 且仓/批/指标、规则快照、规则版本和
severity 均一致才准入。未知原异常或字段不一致直接拒绝，不写恢复行；尚未
送达的合法原异常仍可先暂存恢复，实际出队继续等待原异常确认。这里不能
替代 B 对原持久行的再次校验，也不是 C 已自动生成三次正常采样恢复。
先前 `f862dc…` 是 B `9cf9a5d` 候选期间的普通事件摘要，**不是当前同版值**。
已冻结的旧 pending 行不在这里批量改写；正式接入前须盘点持久行的数量、
版本与 B 回读结果。此对拍不代表真实 B HTTP 送达或 S0/E1 业务验收。

### 2026-10-18 B 恢复增量后的正文版本边界（历史）

B PR #10 当前 `9cf9a5de55d48bf0761655b6f2cda844ec5975f6` 已新增
`CandidateCreate.related_source_event_id`（非恢复候选也由 `model_dump(mode="json")`
输出 `null`），恢复 `temperature_recovered` 以新 ID 引用原异常；B 已在 #5
给出恢复固定摘要 `sha256:d6d1ef23731e60bdcdee94f36c7b271952d071ce7d4d53841cb4dd7cee301c0c`。
当时 `20789…` 是 **7b1cf26 旧 DTO 的向量**；相同普通高温正文在新增
`related_source_event_id:null` 后按 B 规范变为
`sha256:f862dc326de9146c30723cb636cdd8e7fbb99cea3eb464ee09fd2d40cdc9f36c`。
这段只描述 9cf 的暂时实现；当前约定以上方 3f43 的兼容修复为准。

C 新 `f03_body_candidate.py::normalize_f03_body_candidate` 固定 16 字段、UUID、
UTC、温度 °C、有限数值、质量证据和 32768 字节上限，拒绝未知字段；
`outbox_candidate.py::stage_f03_candidate` 必须先规范化再持久冻结，避免 B
默认字段在发送后改变摘要。固定普通/恢复向量及 SQLite 重开后的 outbox
摘要测试已加入。此文件仍是**隔离候选**，没有 B HTTP 调用、真实服务凭据、
正式 C 迁移或 E1 证据。

恢复投递前须先通过 B `by-source` 回读确认原异常已接收且摘要对应 C 冻结
正文。B 新实现对未知原事件返回 422 `invalid/unknown_related_event`；C 不能把
它作为一般可重试 503，也不能抢发恢复。原异常未确认时恢复保持待处理；
同仓/批/指标不匹配仍是永久拒绝。B 当前模块 router 尚未挂主 app，真实
JWT/HTTP 与 PG 跨模块试验待共享接线和独立环境验证。

C 隔离 outbox 现存 `related_source_event_id`，到期扫描及原子抢租仅在本地
原异常已由 C 核对 B 摘要并标为 `delivered` 后让恢复任务出队；原异常尚未
送达或被拒绝时恢复保持 `pending`，**不消耗重试次数**，不会抢先给 B 一个
`unknown_related_event`。这只保证本地投递顺序；没有正式 C 迁移、调度器、
B HTTP 适配或实际 B 回读。原异常永久拒绝时，恢复 pending 需人工审查，
不能自动显示为 resolved/normal。

C 另提供未挂载的 `b_http_candidate.py`：按 B 给定的
`GET /api/v1/candidates/by-source/{source_event_id}` / `POST /api/v1/candidates`
映射 404、201/200/409/422/503 与规范摘要；200 必须有可核对来源 ID 和
`sha256:<hex>`，不全时不标送达。真实网络传输由调用方注入短期 bearer，
禁止 URL 内嵌凭据、重定向转发及非 loopback 明文 HTTP；没有在源码放入真实
凭据或自动启动 worker。401/403/禁用用户等认证失败保留有界重试记录；
发送超时后的按来源回查若再遇鉴权失败，也会记录 `b_auth_unavailable`
并释放租约等待同一冻结正文重试，不误作候选已收。B 目前尚未挂共享根，也未确认服务间身份签发/刷新；
在 A/B 接线及独立 PG/HTTP 验证前，此适配器测试只用本机合成 HTTP 回应，
不能报告为 B 实际接收。

该同步投递的一次尝试最坏会先查自身、发送、超时后回查自身，并在恢复候选时
再查原异常，共四次网络请求。C outbox 当前租约为 30 秒，因此内置传输每次
请求超时限制为至多 5 秒；bearer 回调、真实 B 响应和调度器也必须有界，
否则租约可能先过期并由另一 worker 领取。超时只触发同来源 ID 的持久重试，
不能报告已送达。正式接线应在 PG/B 服务负载下验证耗时和租约配置。

## C 消费 F02 的一批次高温候选端口（2026-10-19，未挂生产调度）

`backend/app/modules/telemetry/rule_evaluation_candidate.py` 从 C 已持久的
`observations`/`evaluation_jobs` 读取观测，不信任设备自报批次；调用方必须从
B 的可信设备/批次关联传 `batch_id`，并把 B 的
`select_rules_for_observation(session, *, warehouse_id, batch_ids,
metric_code, sampled_at) -> RuleSelection` 转为 `model_dump(mode="json")`
后注入。仅支持**一批次、温度 `°C`、F02 `threshold_high` 映射到
F03 `temperature_high`、严格 `>`**。
同一个业务观测只用 `sampled_at` UTC 选择历史规则，保留原始 `raw_text`、
`raw_payload` 和原质量标记；真实高温且缺湿度时仍可生成高温候选，同时
证据保留 `missing_metric:humidity`。无规则、无可用温度或不完整的非高温
观测保持 `unknown`，不能显示为 normal。

示意的 B→C 选择回复（仅端口输入样例，非真实服务回执）：

```json
{"status":"configured","rules":[{"batch_id":"33333333-3333-4333-8333-333333333333","warehouse_id":"22222222-2222-4222-8222-222222222222","rule_version":"temp-v1","snapshot_id":"88888888-8888-4888-8888-888888888888","metric_code":"temperature","unit":"°C","kind":"threshold_high","severity":"warning","comparison":">","threshold":8.0,"recovery":{"comparison":"<=","threshold":7.0,"min_samples":3},"effective_start_at":"2026-10-14T00:00:00Z","effective_end_at":null,"source":"warehouse-manager-ui"}],"reason_code":null}
```

选择器返回 `no_rule` 且空规则时，任务记 `no_rule` 而观测风险仍为 `unknown`；
读库临时失败保留待评估任务以便重试；批次/仓库/单位/级别/时间窗不匹配则
保守阻止候选。高温候选的 `source_event_id` 只由观测 ID、可信批次 ID 和
`temperature_high` 事件种类确定，不随规则快照版本变化；先冻结完整 F03 正文
到 C outbox，再将任务和风险状态提交。两次事务之间崩溃时，下一轮先
复用已冻结候选和正文，不重新选可能改变的规则，也不重复生成业务事件。
同一观测/批次被交错评估为不同版本时，只能留一条 outbox，异文作为冲突
留审计，不生成第二业务事件。本地六项独立 SQLite 回归覆盖高温缺测、
无规则/非高温缺测、非法时间/单位、临时失败、规则不匹配、崩溃重试和
交错版本。测试中 `metadata.create_all` 只作用于
临时测试库；**没有正式 C Alembic、B 实际 SQLModel 调用、HTTP 投递、
后台调度、PG 组合运行或 E1 结论**。

另在现有 `test_telemetry_storage_postgres_candidate.py` 增加同一观测两
评估者交错选择不同规则版本、唯一 outbox/冲突留证及新连接回读的 opt-in
PostgreSQL 回归。它沿用严格 `test-db/freshlink_test/FreshLink-test` 守卫
和每测试独立 schema；C 本机无受控 PG，此项**仅已被本机测试发现并跳过**，
不得把跳过记作 PG 通过。待有隔离 PG 的同学在准确提交上实际运行并
记录结果；此测试仍只建临时 schema，不建立正式共享迁移。

正式接线仍需 B 先发布已审唯一迁移 head 并修复 PR #10 中同文旧行
摘要重放和误恢复；A 协调共享根与调度入口。C 在实际 B 服务/PG 环境
验证后才扩大到同观测多批次、恢复连续样本与生产任务领取。当前单批次
候选不能代替完整告警生命周期或真实通知。

## C 已存观测推进恢复窗口端口（独立候选）

`recovery_observation_candidate.py::advance_saved_recovery_observation_candidate`
按原异常 `source_event_id`、**可信关联的** `batch_id` 和一条已存 C 观测 ID
显式调用。先核对原 F03 冻结正文/摘要、原观测设备与冷库、原采样时间及数值；
原规则必须由 B 按**原异常采样时刻**选回。再从保留的原始载荷重新计算
归一化与质量，拒绝被改写的存储证据，并经持久窗口的版本检查推进。
同一观测重放不会增加样本；错误单位、缺失温度会重置连续窗口；缺历史
规则、缺表或读库故障分别等待、返回可重试结果，均不会宣告恢复。

本端口没有调用 B、后台扫描、共享根挂载或正式迁移；调用者要先从可信
设备/批次绑定取得 `batch_id`，把 B 的历史规则选择结果注入，并按观测
接收顺序逐条调用。窗口合格后仍须按现有 `stage_recovery_page_candidate`
冻结候选、C outbox 可靠投递及 B 原告警 resolved 回读。正式 PG 接线时
需把观测读取与窗口推进置于审核后的事务/锁边界，复核并发原文修改和
设备绑定变更；当前 SQLite 文件库测试不证明这些生产并发性质。

## 2026-10-22：预选高温候选的 A 门禁事务端口

`rule_evaluation_candidate.py::stage_gated_high_candidate(engine, *,
device_id, warehouse_id, stream_epoch, observation_id, batch_id, body, now)`
是当前**显式调用** A `begin_evidence_write` 的 C 高温写入口。B 的历史规则
选择、可信设备/冷库/批次关联和完整 F03 正文构建必须在进入此端口前完成；
不能拿设备上报的批次或未经核实的 epoch 调用。端口取得 A 同设备行锁后，
在同一 Connection/事务核对已存观测归属、冻结 F03 outbox、更新 C 任务与
risk；冲突 ID 记录审计并用 A `mark_uncertain` 留 hold，不会把它标成正常。
无写入的重放回滚门禁 revision，异常则回滚整个事务。锁内不访问 B 网络。

该端口仍未由共享 worker 调用；旧独立 `evaluate_one_batch_candidate` 仍是
隔离试验入口，**不能作为正式自动调度器**。本地 SQLite 单测用 Mock A
门禁覆盖成功/无写重放、错归属、冲突 hold 调用和门禁失败，不能证明真实
PostgreSQL 行锁。A 的唯一 C01 活动候选和真正 PG 锁组合在 PR #54，
正式迁移发布与高温规则/告警联调仍须按其精确头共同审核。恢复窗口、
invalid/冲突接纳、候选投递和发送前复核等其他写入口仍待同版门禁覆盖；
本端口不启用自动恢复，也不表示完整 E1 已通过。
`test_telemetry_gated_high_postgres_candidate.py` 另在 A 的一次性私有 PG schema
内建立测试用 C 表，核实际 A gate + C 高温 outbox/risk/job 同时提交、重放不
增加 revision、错 epoch 拒绝。此测试需要项目 Python3.14 与明确的受控 PG
开关；本人 Windows Python3.13 无法加载完整项目测试配置，待精确提交的
远端门禁真实执行后再记录结果。测试中直接插入合成观测/规则正文，不证明
真实 B 历史规则选择或正式 C01 升级。

## 2026-10-23：B 历史 F02 到 C 高温门禁的显式组合

`LocalBRuleSelectionCandidate(engine)` 用独立短 `Session` 调用 B 的真实
`select_rules_for_observation`，转换为 JSON DTO 后关闭会话。新增
`select_and_stage_gated_high_candidate(engine, *, device_id, warehouse_id,
stream_epoch, observation_id, batch_id, selector, now)` 先读已存 C 观测、按
`sampled_at` 调 F02、核单条 `threshold_high` 规则的批次/仓库/单位/版本/生效
区间，从原文重算质量并构造 `temperature_high` F03 正文；随后才调用现有
`stage_gated_high_candidate`，在 A 同设备门禁下原子写候选、风险及任务。
读取 B 的阶段**不持 A 锁**。调用方仍须提供经认证的设备、冷库、epoch
及可信批次关联；当前没有共享 worker 自动调用此组合。

此入口只处理确认为高温的单批次样本。无规则、完整性不足、常温或不匹配
的历史规则均不写正常/低风险，保留待评估状态并返回 `held_unknown`；规则库
临时失败返回 `retryable_failure`。原始观测和质量标记不变。SQLite 回归
覆盖选择发生在门禁之前、写入/重放、无规则不改风险；Python3.14 远端还
需执行真实 B DTO 到本端口的回归。Mock A 的 SQLite 用例不证明 PG 锁；
原有严格 PG 高温写测试使用合成 B 规则；新增同文件的实际 B F02 服务
用例另验其到 A gate/C 写的组合。两者都不证明完整 E1 或 B F03 实际接收。

## 2026-10-23：C outbox → B 实际路由/服务的接收回读测试

`test_telemetry_b_service_delivery_candidate.py` 在两个隔离 SQLite 文件库中
分别保存 C 原始观测/outbox 和 B 冷库/批次/告警。B 使用实际 events router 与
`receive_candidate`，HTTP 请求由进程内 `TestClient` 转发给现有
`BHttpCandidatePort`；C `deliver_once_candidate` 先按来源查询，再提交冻结 F03。
测试特意在 B 已提交 POST 后丢掉响应，C 再按同一 `source_event_id` 回读摘要，
只有正文摘要一致才把 outbox 标为 delivered；再次运行不再 POST，B 告警一条。
需项目 Python3.14；本人本机3.13仅能验证测试被跳过，远端结果按精确头登记。

这是实际 B 应用服务/数据库逻辑的隔离调用，不是部署后的 HTTP/TCP、真实
MQTT 上报、后台调度、两渠道通知或完整 E1；用于在正式接线前发现 F03 回执
字段/摘要/幂等不兼容。发布时仍需可信服务鉴权和同版 PG/重启/失败联调。

## 2026-10-23：未冻结的恢复候选不得进入通用投递队列

`outbox_candidate.py::_dependency_ready` 现在对带原异常引用的恢复候选额外要求
完整的 A 前沿冻结字段（可信设备/仓库/epoch、正序 admission、水位相等、
revision 非空）以及原异常已投递。`claim_candidate` 与 `due_source_ids_candidate`
共用此条件；旧候选、非门禁 `stage_recovery_page_candidate` 产生的候选和
只有部分冻结字段的行保持 pending、attempt_count 不增加、不会调用 B。
异常候选的领取行为不变。已有单测用**合成完整 stamp**仅检查队列条件与 B
回读语义；不把手工写入的 stamp 当作 A 当前前沿验证。

这仍只是必要条件。恢复发送前必须在 A 同设备锁内核当前 stamp、迟到无效
证据与绑定，并与 B 对已经接受后的更正/重开语义验收；网络发送跨事务，
不能把一次事前读称为线性化。此检查和正式调用尚未完成，自动恢复外发保持
关闭。不会修改活动 C01 迁移或共享根路由。

## 2026-10-23：一批次常温样本的 A 门禁写端口

`rule_evaluation_candidate.py::stage_gated_within_rule_candidate(engine, *,
device_id, warehouse_id, stream_epoch, observation_id, batch_id, selected_rule)`
接收调用方**在锁外按采样时刻选出的 B F02 单条规则**。在 A 同设备写门禁内复核
可信归属、待评估任务、规则批次/仓库/温度单位/版本/生效区间，再用已存原文重算
质量与温度。只有质量恰为 `['ok']`、温度存在且未超过有效阈值时，同事务将
任务记 `within_rule`、风险记 `normal`；缺测、坏原文、过期/缺失规则及真正高温
均保持 pending/unknown，空操作回滚 A revision。不访问 B 网络，不形成 F03。

这是**一个批次**的显式端口；A 的 PR57 caller 当前只处理高温，尚未调用此
端口。多批次不能逐批调用此端口，因为第一批会终结 observation 的唯一任务；
需 C 另提供全批次原子评估或逐批任务模型，A 不得先循环并取第一批结果当全局
正常。恢复窗口与发送前前沿复核仍独立待验。

## 2026-10-24：已持久恢复窗口的门禁分页入口

`recovery_tick_candidate.py::LocalBHistoricalRecoveryRuleCandidate(engine)` 从 C
原异常冻结正文取可信仓/批次与原采样时刻，再经 B 真实 F02 历史服务选规则；
`stage_gated_recovery_page_candidate(engine, *, historical_rules,
frontier_epochs, now, after=None, limit=50)` 只扫描已合格的持久窗口，先核
原异常已投递和可信 A epoch，再在门禁**外**读取 B 历史规则，最终调用现有
`stage_gated_recovery_candidate` 在 A 设备锁同一事务冻结恢复 F03 与完整前沿。
无 epoch/缺规则/原异常未交付/读故障分别保持 held/wait/retry，不回退到旧
`stage_recovery_page_candidate` 无门禁写入口。分页游标只用于本次扫描，未来
重试必须从头扫描等待项；不会自行安装后台任务。

本轮 SQLite 回归用 Mock A gate/stamp 核选规则先于门禁、冻结/重复及缺 epoch
不写；另有项目 Python3.14 专用测试读取 B 实际 F02 服务并核原快照/恢复条件。
Mock/SQLite 不证明 PG 行锁，且**队列完整戳仅为发送的必要条件**；A 当前
PR57 尚未调用此恢复页，C 也未实现发送瞬间当前戳和晚到坏证据复核、B 已接
受后重开，因此自动恢复外发继续关闭、完整 E1 未验收。

## 2026-10-24：一个观测的全部有效批次原子评价端口

`multi_batch_candidate.py::select_and_stage_gated_multi_batch_candidate(engine, *,
device_id, warehouse_id, stream_epoch, observation_id, now)` 是供 A 明确调用的
组合入口。它在**门禁外**读已存观测的 UTC `sampled_at`、设备当前可信绑定、
B 的全部有效批次及同一时刻的 F02 温度规则；没有有效批次、任一批缺规则、
规则错单位/身份/时间时返回 `held_unknown`，不把首批当全部完成。
`stage_gated_multi_batch_candidate(engine, *, device_id, warehouse_id,
stream_epoch, observation_id, batch_ids, selected_rules, now)` 是不访问 B 的写端口。
调用方预选的 `batch_ids` 必须是**完整有效集合**，每批恰好一条 F02 规则。

写端口自行持有外层 `Engine` 事务：`begin_evidence_write` → 锁定唯一观测任务 →
逐批核原文/质量/规则并构造确定性 `source_event_id` → 同一 Connection 写所有
高温 F03 outbox → 一次更新全局任务及含各批结果的风险 JSON → 一次提交。
混合高温与正常分别记录；缺湿度时高温仍可形成候选，低于阈值的批次明确
`unknown`，不显示正常。任何必要写入异常都会回滚所有本轮候选、任务与 A
revision；重放已提交任务返回 `not_due`，不再遗漏第二批或生成第二套来源 ID。
DB 异常从写端口抛出交 A 调用方重试；组合入口把规则库/存储异常归为
`retryable_failure`。没有正式调度、根路由或通知外发。

A 最小接线位置是现有 `GatedHighWorker.evaluate` 的 `len(active) != 1` 分支：
在 PR58 发布并同步 C 端口后，改为将**全部**有效批次一次传入上述组合入口，
不能循环单批 `stage_gated_high_candidate`/`stage_gated_within_rule_candidate`。
返回的 `source_event_ids` 仅表示已提交的高温候选，不是 B 已接收、投递或实收。
当前 SQLite 单测 Mock A 写门禁，真实 PG 同头锁/事务、A caller 和 B F02 多批
组合仍需项目 Python3.14/受控 PG 复测；在此之前端口是可审核候选，不是 E1。

## 2026-10-24：恢复发送前只读新鲜度检查（不启用发送）

`recovery_freshness_candidate.check_recovery_freshness_candidate(engine, *,
recovery_source_event_id, device_id, warehouse_id, stream_epoch)` 在受控 PG
事务中取得 A 同设备前沿锁，核冻结的设备/仓库/epoch/接纳序列/证据 revision
仍与当前一致；再核原异常已投递、恢复窗口仍合格、冻结正文与窗口观测集合一致、
原始及恢复观测的质量/温度/时间，以及原异常接收后有无未解决的 invalid/conflict
接收或重复冲突。返回 `ready_at_check`、`held` 或 `retryable_failure`，原因仅固定错误码。
该入口不认领 outbox、不访问 B、不发网络请求、不把告警标记 resolved。
发前检查还会重算冻结恢复正文与原异常正文各自的 F03 摘要，拒绝入库后被改动的
正文；即使摘要与正文一致，原事件也必须仍是同来源、同观测、同批次和同采样
时刻的 `temperature_high`。这些都是必要的持久证据检查，不能替代 B 已受理恢复
后的更正事件契约。

锁在检查事务结束后释放，因此 `ready_at_check` 只代表该时点；后续新证据仍可能
到达。正式恢复 sender 必须设计认领与发送前复核的调用顺序，并落实 B 对已受理
恢复后晚到坏证据的更正事件语义及关联 ID/通知展示，才能启用。当前 A 的
`deliver-high` 只处理高温，恢复仍排除；不能把此只读端口或合成测试表述为 E1
自动恢复闭环。项目 Python3.14/专属 PG 的精确测试结果见同头 CI/证据，未实跑
前保持待测。
