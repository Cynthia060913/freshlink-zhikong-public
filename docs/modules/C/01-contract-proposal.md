# C 首轮接口提案：观测质量、查询与候选

状态：草案，供 A/B/S0 确认；不是已实现的公共 API 或数据库结构。本文首稿依据旧 main 的 contracts/INTERFACES.md v0.1 和当时 PR #4 的 v4 说明。2026-10-12 远程 main 已含 A01/v4；C 当前实现 `process_batch`、A envelope 纯投影、内存筛选/排序/样例续页和固定样例 CSV 字节预览。固定阈值只是测试规则；数据库事务、正式查询/导出、鉴权、B 规则/告警和 MQTT 均未实现。以下按日期记录的旧状态应结合 [#9 STATUS C](https://github.com/Cynthia060913/freshlink-zhikong/issues/9#issuecomment-6106008709) 阅读，提案仍待三方确认。

## A → C 观测提案

每条 JSON 观测包含 schema_version、device_id、warehouse_id、message_id、sampled_at、received_at、metrics。device_id、warehouse_id、message_id 使用 UUID 字符串。sampled_at 和 received_at 必须为带 UTC 时区的 ISO 8601 时间（Z 或 +00:00），分别代表设备采样和服务接收时间。metrics 是 key/value/unit 条目数组；本轮识别 temperature/°C 和 humidity/%RH。其他条目保留在 raw 中，待设备契约确认支持范围。A 负责设备身份/归属鉴权；C 不建立公开绕过入口。

固定输入与实际期望输出分别见 examples/telemetry-input.json 和 examples/telemetry-output.json。输出每条均包含完整 raw、quality 标记、duplicate 状态、normalized 和 risk。raw 是原 JSON 深拷贝，非法输入及高温也不会被删除。normalized 只包含已通过校验的值；错误单位绝不猜测转换。temperature 没有人为高温上限；humidity 有 0–100 %RH 范围校验。

质量标记以 ok、missing_field、missing_metric、missing_value、invalid_id、invalid_time、invalid_unit、invalid_value、invalid_range、duplicate_message、conflicting_message 等明确区分。输入不是对象时返回 invalid_observation。重复键提案为 (device_id, message_id)；同键且采样内容一致判重复，received_at 的变化不构成冲突；同键测量或采样内容变化判冲突。两种情况都保留每次原始输入，批内不重复输出正常/告警结论。正式跨重启幂等需要数据库约束与事务，尚未实现。A 请确认重发时哪些字段保证不变及 metrics 顺序语义。

risk.state 为 normal、candidate、unknown。只有完整有效观测、存在明确阈值且温度未超时才可能 normal；无规则、缺失或无效温度均为 unknown。可追溯的真实高温即使湿度缺失仍给 candidate，并同时保留质量缺口。重复/冲突不再次生成候选。阈值比较为 temperature > threshold_c；阈值相等属于未超，但正式比较符号由 B 规则版本确认。阈值参数不是 B 的正式规则快照，candidate 也不是 B 的已持久化告警。

失败行为：无效观测返回逐条质量标记而不丢原始值；顶层批次不是 JSON 数组、无效阈值参数均抛 ValueError。纯处理的 `schema_version` 必须是非空、无首尾空格的字符串；生产允许版本列表仍待共同确认，不能把历史 `draft-1` 当成已获准的生产版本。正式入库、鉴权、批次匹配、错误响应码和事务失败补偿待共同契约确认。

## C → B 候选提案（未实现）

建议在持久化观测后由 C 服务生成稳定 source_event_id，附 observation_ids、warehouse_id、batch_ids、occurred_at、kind、severity、evidence、algorithm_version、rule_version。evidence 应含原始温度、单位、阈值及观测质量；缺规则/缺测不伪装 normal。按 source_event_id 幂等重试，B 明确接收/拒绝及永久失败语义。当前纯函数只输出局部 candidate，尚无 observation_id、批次关联或可重试发送。

## B → C 规则快照提案（未实现）

需要 batch_id、warehouse_id、品种、有效时间段、rule_version、温湿度范围及来源。C 按 sampled_at 选择只读快照，不偷偷重算旧历史；无匹配规则返回待配置/数据不足。B 请确认规则边界（> 或 >=）、恢复条件、批次多对一归属及快照获取签名。

## C 查询/分页/CSV 服务提案（未实现）

建议服务签名 query_observations(device_id?, warehouse_id?, sampled_from?, sampled_to?, quality?, cursor?, limit) -> page；export_observations_csv(同一筛选条件) -> CSV 字节流。统一 UTC 时间范围、稳定排序键 (sampled_at, observation_id)、明确游标和上限。实时视图与历史/CSV 同源、同质量状态；CSV 包含 raw 值与单位、normalized 值、sampled_at、received_at 和质量标记。页面记录数与同条件导出记录数要实测核对；空结果不代表低风险。鉴权复用现有登录。具体 HTTP 路径、响应码、分页上限和列顺序待 A/B/集成 Agent 确认。

## 独立边界样例与待确认约定（2026-10-11）

`examples/telemetry-contract-proposal.json` 是提案样例，不是已经开放的 API、已存储记录或 B 实际告警。其七条 A→C 观测依次表示：正常、同键 UTC 等价重发、同键温度冲突、独立消息真实升温、温度缺值、非法采样时间、错误温度单位。纯函数测试只验证前述七条的批内质量、重复状态与候选判定；跨重启去重、入库事务和 B 接收尚未实现。

- **身份与重发**：提议键为 `(device_id, message_id)`。同键重发允许 `received_at` 变化及 `Z`/`+00:00` 的等价 UTC 写法；`sampled_at`、仓库或测量变化视为冲突并保留两份 raw，不再次发候选。换设备的相同 `message_id` 是另一键。metrics 顺序是否有语义、schema_version 是否允许变更，仍由 A 确认。未知但合法的顶层扩展字段也属于消息内容；同一消息 ID 下改动它们按冲突处理，字典成员顺序仍不影响重复判断。当前 `process_batch` 的判定只在一次调用内有效。
- **规则快照**：样例以 `rule_version=example-r1`、`temperature_max_c=8` 和严格 `>` 展示阈值边界；恰好 8 °C 不触发候选，8.01 °C 触发。正式 B 规则的比较符号、生效区间、批次归属和版本选择必须确认。无规则仍为 unknown；样例规则并未进入纯函数。
- **候选交付**：样例中的 `source_event_id`、`observation_ids` 和 `rule_version` 是未来持久化后的建议结构。重试应沿用同一 `source_event_id`；超时后先按键核对，避免生成第二条告警。B 的接收、已存在、永久拒绝及可重试结果待约定；`severity` 仍是示例值，不代表已定义等级。
- **查询与 CSV**：样例的 `page.filters` 与 `csv.filters` 完全相同；提议时间窗口为 `[sampled_from, sampled_to)` UTC，分页按 `(sampled_at, observation_id)` 稳定排序，CSV 不受当前页 `limit` 限制。权限、游标编码、列顺序、最大导出量及空结果/无数据状态待集成确认。样例没有实际查询或 CSV 记录。

## 对 A 的 RC1 提案逐项回应（2026-10-11，仍待 S0 三方确认）

A 的提案见 PR #4 的 `docs/modules/A/01-contract-proposal.md`（提交 `7f815681adb027f1757e59b1b822740d309df173`）；此处是 C 对可实现性的答复，不是已发布的公共契约或正式服务。

- **版本/来源**：建议接受契约文档 `0.2.0-rc1` 与未来生产观测 `schema_version="1.0"` 两层版本。历史 `draft-1` 七条样例不自动变成正式生产输入；另增 `examples/telemetry-rc1-proposal.json` 作为四条提案样例。当前 `process_batch` 仍只要求非空版本；生产允许版本列表、拒绝审计、体积与保留上限需 A1 和三方契约后在服务边界实现。
- **设备与重发**：原则接受 A 的 `iot/ingestion.py` 认证/绑定后调用 C `telemetry/service.py::ingest_observation(envelope)`、A 生成首次 `received_at`、C 事务生成首条 `observation_id` 和各次接收证据的分工。两处服务目前均不存在；A01 当前拒绝 warehouse_id，需 B 的有效绑定后才可接生产链。A 请明确 envelope 中 raw payload、认证身份、仓库绑定、首次 received_at 的字段与大小上限，以及失败时由谁保存拒绝审计。
- **内容等价**：同意以 `(device_id,message_id)` 为键，忽略 `received_at`；UTC `Z` 与 `+00:00` 等价；唯一 key 的 metrics 数组顺序无业务含义，同键内容变化为冲突。C 纯处理已按本提案规范排序唯一 key 条目；重复 key（包括未知 key）标记 `duplicate_metric`，若温度 key 重复则不任选一个值作规范温度，raw 原样保留、风险 unknown。批内结果不代表跨重启事务幂等。
- **错误结果**：原则接受 `accepted_new/accepted_duplicate/conflict/invalid/retryable_failure` 以及稳定 observation ID/reason_code；只有数据库事务成功或同键已存在才让 A 清待发任务。C 还没有正式表、事务、服务或 outbox。请 A 确认超时重试的 envelope/received_at 规则和 reason_code 集合；C 将在已合并 A01→B head 上做事务与跨重启测试。
- **查询/CSV**：UTC 采样与接收时间各自半开范围及 AND、非法 sampled_at 的规范列 NULL 且 raw 保留、无采样范围时按质量可找回，均可实现。接受稳定 NULLS LAST 排序、接收水位与签名游标作为待验收目标；具体签名密钥来源/轮换、过期与权限版本由集成方确定。建议的 1–100 页长、10000 行全量上限和列序可用作 RC1 测试向量，仍待 B 仓库 ACL 与三方确认。本段写于 2026-10-11；后续已增加纯排序、样例续页与固定内存 CSV 字节预览，但生产游标、鉴权、数据库查询和正式导出仍未实现。
- **CSV 文本处理待选**：同意标准库 `csv.writer` 处理结构、已验证的负温数值按数值导出，raw 仍在受权限保护的详情。A 建议的单引号前缀不能直接宣称对所有表格软件/重存场景安全；[OWASP CSV Injection](https://owasp.org/www-community/attacks/CSV_Injection) 明示此限制。请 A/集成方确认首版主要用于人工电子表格还是机器再导入，然后冻结可复测的文本保护规则；C 不在未定用途下给出“通用安全”实现。

B 的规则快照、批次关联、候选 kind/severity、恢复阈值与 C→B 接收结果仍待 B 在 #5/#8 用 JSON/签名回应。样例阈值 8 °C 和 `fixture-threshold-1` 不会直接发送成正式业务规则。

## 对 B 于 S0 #5 的规则/候选答复（2026-10-11；仍是提案）

B 的原文见 [#5 评论 6108448780](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6108448780)，当时 B 分支 HEAD 为 `447ac38a48a853d83b0afe950b11a945b170ff36`。B 的现有 `docs/modules/B/01-contract-proposal.md` 仍写旧签名和旧样例；评论中的新字段尚未写入已发布的正式共同契约，也未见 B1 的规则服务/迁移。以下仅确定 C 的可实现条件。

1. **快照返回形状**：接受有序规则条目 list 及每条带版本的方向，但 B 的旧提案返回 `{rules:[],hint:...}`，新评论又称返回 list。建议只读服务固定返回 `{status:"configured"|"no_rule",rules:[...]}`；无规则是同形空数组，数据库/权限/版本歧义为独立错误，不能被空数组吞掉。请 B 给正式签名、输入仓库/批次/采样时刻、批次为零或多个时的判定及错误码。C 直接消费 B 的服务结果，不由 A 在外层代定 `NOT_CONFIGURED`。
2. **时间与不可变性**：C 要按观测 `sampled_at` 取当时适用的 `rule_version`，并将其与候选保存；历史候选不能因当前规则变化而偷偷重算。B 同时提出“禁止 UPDATE/DELETE”和“旧版 `is_current=false`/`superseded_by` 更新”，无法同时成立；仅查 current 也无法重放旧观测。请 B 选定可验证的版本替代关系和历史读取语义，明确 UTC 带时区、`[effective_from,effective_to)`、相同 batch+metric 的区间重叠拒绝。数据库时间范围需与 UTC 带时区约定一致。
3. **签名与规则字段**：`sha256(version_id + metric_codes + thresholds)` 未覆盖单位、比较符号、生效区间、候选 kind/severity、恢复条件，也没有规范序列化/密钥语义。C 可把版本 ID 当可追溯引用，但不会将该摘要当防篡改或完整规则身份保证。请 B 给出每条规则的 metric_code、unit、operator、max/min、effective interval、candidate_kind、severity、recovery condition/source，并明确一版本多批次关联；缺 metric 不可降级为 normal。
4. **候选类型与恢复**：接受 `temperature > max`、等于阈值不触发的首版测试目标；正式边界以版本化规则为准。B 的 `threshold_high` 对温度/湿度丢失指标语义；C 建议候选保留 `metric_code` 与 `kind=temperature_high|temperature_recovered|data_quality`（或 B 提供无损映射），与 A 的设备事件枚举分开。当前 C 只计算高温候选，不产生恢复；恢复要有明确持续时间/连续样本、缺测和乱序处理，另以新 `source_event_id` 关联原异常事件 ID，不能仅把原 ID 作为新恢复记录的幂等键。
5. **C→B 投递**：同意 B 用 `source_event_id` 幂等、同键同内容返回已接受、同键不同内容返回 conflict、持久临时故障可重试及按键查询。C 将在正式迁移后把候选与待投递状态持久化，同一 ID 重试；B 的通知内部重试不能替代 C→B 的投递重试。超时先按键核对再重试。conflict 应保留不可变原事件及冲突接收证据；如果 B 在同一事件行上改 `conflict=true`，需说明与其 append-only 承诺如何兼容。请 B 固定 `accepted_new/accepted_duplicate/conflict/invalid/retryable_failure`、稳定对象 ID、reason_code、查询权限/未找到语义、`Retry-After` 及 `raw_payload` 用途和上限。服务接受不等于通知服务接受或终端实收。
6. **正式接线**：C 不创建 B 的 `mapping.py`、规则路由或共同根路由。B 新评论写 `backend/app/main.py`，而已读仓库实际根 API 接线在 `backend/app/api/main.py`；请集成方以合并后的实际代码确认导入点。正式 C 迁移仍接已合并 A01→B 的唯一 Alembic head，不接受另一条并行迁移链。

最小正例应同时给出 B→C `{warehouse_id,batch_ids,sampled_at,metric_code}` 查询、当时规则版本/有效区间及 C→B 稳定事件 ID、observation_ids、rule_version、evidence；反例至少为无规则、重叠规则、同 ID 同内容重试、同 ID 改内容、接收超时后查询与恢复关联。上述 JSON/签名需 B 在 #5 提供最终字段后共同冻结，现有 C 固定样例仍是纯处理测试。

## 对 A 最新版接收封装与 CSV 补答（2026-10-11；待三方确认）

已读 [A 新版第 5 节](https://github.com/Cynthia060913/freshlink-zhikong/blob/caf8e3a6273737623873be60efca9651a5f4fc9e/docs/modules/A/01-contract-proposal.md)及 [R01–R11 完整验收矩阵](https://github.com/Cynthia060913/freshlink-zhikong/blob/caf8e3a6273737623873be60efca9651a5f4fc9e/docs/team/v4/09-完整目标与参赛验收矩阵.md)。C 原则接受 A 的认证后内部 envelope：`raw_payload`、原 UTF-8 `raw_text`、`authenticated_device_id`、有效 `warehouse_id`、A 生成的 UTC `received_at` 和每次接收的 `reception_id`。同一次数据库调用重试复用 reception_id；新的设备网络接收可有新 ID/时间。C 首次事务生成 observation_id，同键重复返回首条 ID，冲突仍保留新 reception 证据；不能由 A 预报观测已持久化。

**原始边界需明确**：现有固定 `telemetry-rc1-proposal.json` 是纯函数的“处理输入”，内含 `received_at`；真实设备原包不应有这个服务时间。新 `telemetry-envelope-rc1-proposal.json` 分开保存未增补的设备 `raw_text/raw_payload` 和 A 的可信接收元数据。生产 C 需持久保存 exact raw_text/解析快照与 reception，处理时可合成可信 `received_at` 输入，但不能把合成对象冒充设备原包。设备若自带 `received_at`，A/C 必须拒绝或明确忽略并留证，绝不信任它。请 A 确认 MQTT 载荷是否允许此字段、`raw_text` 非 JSON/编码错误由谁记拒绝审计，以及 `warehouse_id` 被省略时是否由 A 绑定填充。样例不代表服务或数据库已实现。

C 可将 32768 UTF-8 字节原包、最多 16 个 metrics、key/unit 最多 64 字符作为首版 **待冻结的上限测试向量**；越界不截断生成 normal。A 负责认证/主题/绑定和无效原包有界审计，C 负责合法 envelope 的结构校验、事务原始留存、质量与 reception。C 的永久结果可用 `invalid_payload/invalid_schema/invalid_identity/invalid_metrics/payload_too_large/content_conflict`，成功 `stored/duplicate`，暂时存储错误 `storage_unavailable/internal_error`；`queue_full` 是 A 待发队列边界，不能由 C 假装已经落库。最终 status/reason_code 的一一映射、超时与提交后响应丢失验收待共同契约测试。当前 `process_batch` 已对 metrics 数量及 key/unit 字符长度做 RC1 纯质量标记；结构越界时 risk 为 unknown、reason 为 invalid_metric_shape，真实高温数值和原文仍保留，不截断成 normal 或直接生成阈值候选。A 在 PR #14 第 7 节提出正式 ingest 返回 invalid，尚待 C 数据库/审计服务实现与共同冻结；纯函数不冒充正式返回。生产版本限制、正式服务返回和跨重启行为仍未实现。

**查询与导出方向**：首版活跃登录者可读、管理员写且暂无逐仓 ACL 是 A 的可实现提议；C 接受作为明确披露的 RC1 目标，仍请 B 确认仓库权限。查询/计数/CSV 复用授权，签名游标由目的隔离的 SECRET_KEY 子密钥生成并绑用户、权限版本、规范筛选、排序、接收水位、末键与 15 分钟有效期；每次请求仍重新鉴权，签名不是授权。密钥/轮换不进入样例或日志。当前 `page_preview_by_sampled_at` 的样例 ID 标记完全不具备这些属性。

A 已确定首版 CSV 目标为**人工查看**。C 原则接受 `csv.writer(QUOTE_ALL)`、UTF-8 BOM、规范字段、有限数值（负温保留数字）、未知空值与 quality/risk_state 同步；外部危险文本以单引号前缀做人工查看展示转义、原文留在有权限详情，不把展示 CSV 称作无损机器回导。具体外部字符串判定、目标表格软件手工打开、重存行为以及最大 10000 行的拒绝路径须实测；没有任何通用安全保证。仍待正式契约和页面/CSV 同授权后实现导出字节流。

## 2026-10-12：对 A PR #14 第 7 节的 C 消费方答复（待共同签收）

来源：A 提案提交 `7531728ff198d9c5b09e25b82e8c19281baf05c2` 的 `01-contract-proposal.md` 第 7 节和 `contracts/examples/a-query-integration-rc1.json`；以下是 C 接受的**目标**，不是当前已开放服务。

- **F01 ACCEPT 目标**：A 认证身份、绑定仓库、UTF-8/JSON/32 层/32768 字节先校验；C 校验合法 JSON 的最多 16 个 metrics、key/unit 最长 64 字符。结构越界的正式 ingest 应返回 `invalid` 与有界原因，不能写成正常或生成告警。当前纯处理标 `invalid_metric_shape`、risk `unknown`，保留完整 raw 与可见真实高温；正式拒绝证据的保存位置、上限和事务行为待持久服务实现。重发内容等价包含未知顶层扩展；接收时间独立，成员顺序不构成冲突。
- **F06 ACCEPT 候选签名/路径**：`query_observations(actor, filters, limit=20, cursor=None) -> ObservationPage`；`export_observations_csv(actor, filters, received_watermark) -> CSVStream`。目标 GET `/api/v1/telemetry/observations/`、`/api/v1/telemetry/observations/export.csv`、`/api/v1/telemetry/observations/{observation_id}`。C router 相对前缀 `/telemetry/observations`，A 在 `backend/app/api/main.py` 接一次；静态 export 路由先于动态详情。当前无 C router/数据库接口。
- **Page 候选形状**：`{items,total,next_cursor,received_watermark}`；空结果 `{items:[],total:0,next_cursor:null,received_watermark:"…Z"}`。建议每个 `ObservationPublic` 包含 `observation_id,device_id,warehouse_id,sampled_at,received_at,temperature_c,humidity_percent,quality,risk_state,rule_version`；坏采样时 `sampled_at=null`，缺测 `temperature_c=null` 且 `risk_state=unknown`。raw/exact raw_text 只在受权详情，不放无界列表/CSV。DTO 字段属 C 提案，待 A/B 核对。
- **权限/错误目标**：actor 必须来自现有 CurrentUser。复用现有 401 无 Bearer、403 坏令牌、400 inactive、404 无权/不存在；参数坏 422，签名/过期/跨用户或筛选变化 `invalid_cursor` 422；CSV 无水位 `received_watermark_required`、超过 10000 行 `export_row_limit` 422；存储不可用 `storage_unavailable` 503 加 `Retry-After:30`。按 A 的 purpose 字符串 `freshlink/telemetry-cursor/v1` 从 SECRET_KEY 派生 HMAC-SHA256 子密钥，规范 JSON，actor/角色/政策/筛选/排序/水位/末键入 15 分钟游标；每次重新鉴权，密钥与游标不记公开日志。B 的首版仓库权限政策仍未确认，不能以签名代替授权。
- **CSV ACCEPT 人工查看候选**：14 列顺序沿当前预览，BOM/QUOTE_ALL、空结果只有表头、超过上限先拒绝；外部文本若去前导空白后以半角或全角 `= + - @` 起始、或开头为控制字符，加单引号。有限数值负温保留数字。当前纯预览已覆盖全角向量；目标表格软件打开与重存、正式鉴权/水位/查询一致性仍未实测。不宣称可无损机器回导或通用电子表格安全。
- **一项待统一的水位语义**：只有接收时间上界不能保证跨请求固定结果集：首次查询时仍在入库事务中的旧 `received_at` 记录可能稍后提交，并在下一页/CSV出现。建议 C 在正式分页/导出时增加由服务维护的固定结果快照标识并绑定游标/导出，或三方明确把“同水位”定义为最终一致而不承诺固定 `total`/页与导出集合。请 A/B 选择验收语义；C 不私改共享契约。

F02/F03 的历史规则、候选接收与 B 权限仍按上文标 NEED CHANGE；C 迁移待 B 合并后的唯一 head。此段不改变 PR #11 为纯处理/预览的实际范围。

## 2026-10-12：消费 B 的 S0 答复与 A 固定快照修正（更新候选，未实施）

已读 [B F01—F06 答复](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6127332268)、[A F06 并发补答](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6127247857)、[A PR #17 第 8 节](https://github.com/Cynthia060913/freshlink-zhikong/blob/63fee5cb87587208877b31f46c7d694fb9c4979d/docs/modules/A/01-contract-proposal.md)。本节修订上方尚待 B 回答/仅接收水位的旧状态；这些仍是 `0.2.0-rc1` **目标**，不是 C/B 数据库或 HTTP 已存在。

- **F01：ACCEPT B 的仓库边界**。A 负责可信设备绑定；B 候选入账再核仓库存在性，未知 `invalid/unknown_warehouse`；B v1 不提供物理删除已绑定仓库。C 的原文/接收证据和无效结构边界不变。
- **F02：ACCEPT B 的单一候选签名及统一 envelope 形状**：`select_rules_for_observation(session, *, warehouse_id, batch_ids, metric_code, sampled_at) -> RuleSelection`，返回 `{status:"configured"|"no_rule"|"invalid",rules:[...],reason_code?}`；时间为 aware UTC 半开区间，逐批按采样时刻独立选历史 `rule_version`/`snapshot_id`，未命中批次由 C 保持 unknown，不能用当前规则替换。单位 `°C/%RH`，严格 `>` 示例与连续 3 有效样本恢复目标可消费。B 需补多批不同版本、重叠拒绝/非法区间 JSON 与可执行契约检查；B3 选择服务及数据库约束尚未实现。C 后续候选宜显式携带 `rule_snapshot_id`，避免仅字符串 `rule_version` 在多批/重名时无法无歧义追溯；请 B 确认字段。
- **F03：ACCEPT B 的五类状态与恢复新 ID 目标，NEED CHANGE 一处超时查无语义**。C 首次持久化稳定 `source_event_id`，B 同键同内容回 `accepted_duplicate`、改内容 `conflict`，恢复新 ID + `related_source_event_id`。B 的文字“超时先按源查询，只有 retryable_failure 重试；未找到且非暂时错误按 invalid”不足以处理 **调用超时 → 查询返回 not_found**：应以**同一** source_event_id 重试（有界退避），不能转永久 invalid 让候选丢失。若查询命中，应返回可供 C 核对的规范内容摘要/候选字段，或 C 对同 ID 原文重投由 B 回 duplicate/conflict；只给 event_id 不能识别同 ID 内容冲突。请 B 固定这两种返回与重复/冲突/无效/暂时故障 JSON；B3 接收服务/outbox 尚未实现。
- **F06：ACCEPT A 的固定服务端结果快照候选**，取代只用 `received_watermark` 的旧说法。首查询短一致性事务固定有序、不可变的 observation+投影版本成员，返回 `snapshot_id`；游标签名绑定它，CSV 签名变为 `export_observations_csv(actor, filters, received_watermark, *, snapshot_id)`，后续页/CSV 取同一成员与版本、同一授权；TTL 900 秒、最多 10000 行、超过拒绝不截断。C 建议首版上限**每 actor 4 个、全局 64 个活跃快照**，并发达到资源上限不悄悄逐出未到期快照，返回 HTTP 429 `snapshot_capacity` + `Retry-After:30`；请 A/B 确认这个新增错误与容量值。快照在撤权/角色或政策改变时失效，不能绕过重新鉴权。以短 PostgreSQL 一致可见事务落成员，T1 旧 `received_at` 晚提交不得混入旧快照；不持有跨 HTTP 长事务。质量/规则投影初版不可变；若后来重算，需追加投影版本并让快照固定版本，不能只留 observation ID 读当前可变值。
- **F06 非空/失败 JSON**：`examples/query-snapshot-rc1-proposal.json` 给出两条固定观测（真实高温候选、缺温 unknown）、同筛选 CSV 请求、`snapshot_required/invalid_snapshot/snapshot_row_limit` 错误及容量错误提案；测试只核该人工样例与现有纯处理固定输入一致，**不测试快照事务、HTTP、授权或实际 CSV**。B 接受首版活跃登录用户读/管理员写、无逐仓 ACL；这一限制需 UI/材料明示。

正式观测/reception/快照/候选 outbox 表和迁移仍须待 B 合并唯一 Alembic head、共同契约发布后实施。B PR #10 当前未合并，不能用其独立单测当 C 入库依赖已就绪。
