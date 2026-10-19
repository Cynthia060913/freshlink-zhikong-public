# C 共享接线请求

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

### 2026-10-18 B 恢复增量后的正文版本边界

B PR #10 当前 `9cf9a5de55d48bf0761655b6f2cda844ec5975f6` 已新增
`CandidateCreate.related_source_event_id`（非恢复候选也由 `model_dump(mode="json")`
输出 `null`），恢复 `temperature_recovered` 以新 ID 引用原异常；B 已在 #5
给出恢复固定摘要 `sha256:d6d1ef23731e60bdcdee94f36c7b271952d071ce7d4d53841cb4dd7cee301c0c`。
上段 `20789…` 是 **7b1cf26 旧 DTO 的历史向量**；相同普通高温正文在新增
`related_source_event_id:null` 后按 B 规范变为
`sha256:f862dc326de9146c30723cb636cdd8e7fbb99cea3eb464ee09fd2d40cdc9f36c`。
不得拿旧摘要核对新 B 版本。

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
凭据或自动启动 worker。401/403/禁用用户等认证失败保留有界重试记录，
不误作候选已收。B 目前尚未挂共享根，也未确认服务间身份签发/刷新；
在 A/B 接线及独立 PG/HTTP 验证前，此适配器测试只用本机合成 HTTP 回应，
不能报告为 B 实际接收。
