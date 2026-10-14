# A 对接答复及最小契约 RC1 建议

日期：2026-10-11。任务 S0/A1；状态：提案、待三方确认，不是已开放接口、冻结契约或已完成 MQTT。

2026-10-12 当前补答见第 7 节：已核对 C 在 S0 的逐项接受与待定项，补 HTTP 路径、实际认证错误、签名游标和 CSV 单一目标。本文件仍为候选；第 7 节明确替换的旧提议不再并列使用。

已读 B 的 98a25388eb12f98fc4fd10cc26cb09003a20d17f 提案与 C 的 d8fc900fde81522899cc34991ef58cbf937786de 提案、S0 两条具体问题。C 后续纯筛选提交 eb469d0304e7e18beb306c673514a4521751f3fa 已见；未独立复跑其测试。A01 当前拒绝 warehouse_id，不在本轮绕过；正式绑定等 B 仓库服务、三方确认和对应测试。

## 1. A → C：身份、来源、重发与时间

建议契约文档版本 `0.2.0-rc1`，遥测 `schema_version="1.0"`。C 已有 `draft-1` 是测试草案，不静默兼容为正式生产版本；请 C 明确确认并保留旧 fixture 为历史，另增正式版本样例。未知生产版本进入有界拒绝审计，不更新在线状态或生成正常值。

计划调用位置：`iot/ingestion.py` 收到 MQTT 后核对 topic、设备凭据、设备 ID、当前绑定；通过后调用 `telemetry/service.py::ingest_observation(envelope)`。两处目前未实现；不是新增对外匿名 HTTP 接口。A 不直接写 C 表、UI 不访问数据库。

- device_id 由 A 注册后的 PostgreSQL 主键提供，不使用设备编号替代 UUID；warehouse_id 由 B 仓库主键提供、A 从有效绑定读取。载荷声明不一致为永久身份拒绝，不信任设备自行改仓库。未注册/未绑定/停用均不入正常业务遥测，不补虚构仓库。
- 每次采样由仿真器生成 UUID message_id，同一采样重发必须沿用 ID 和内容；新采样即使同温度也用新 ID。重试队列必须留存原包，跨进程重启不能重新生成待发 ID。
- A 在已认证消息首次进入服务边界时生成 UTC received_at；设备不能指定该值。数据库重试使用同一接收封装；网络重发可有新 received_at。
- sampled_at 是设备的 UTC 采样时间；Z 与 +00:00 等价。非法/无时区/+08:00 采样字段保留 raw 并标记质量，不丢合法设备的整条原始观测，不假设本地时区修正。
- 去重键 (device_id, message_id)。忽略 received_at，比对 schema_version、warehouse_id、归一为同一 UTC 瞬时的 sampled_at、测量内容。metrics 数组顺序无业务含义；唯一 key 条目按 key 规范比较；同一包重复 key 是 invalid，不取最后一个。同键改仓库/版本/采样/测量为 conflict，保留各次 raw、不覆盖首条、不再生成候选。
- metrics 为 key/value/unit 数组；首轮 temperature/°C、humidity/%RH。原始非法值、未知项/单位保留；不猜单位转换、不把真实高温修成正常。power/W、energy/kWh 后续需显式扩展测试，不假称 C 当前支持。
- 首条 observation_id 由 C 事务生成并持久化；每次重发接收证据与首条观测关联，允许独立 reception_id。同键唯一约束、内容比较、原始重发证据在事务内处理；仅内存 set 不算跨重启幂等。
- A 正常输出合法单位；错误样例仅作为故障注入。首次 E1 用真实时间、simulation_speed=1；加速实验元信息单独记录，不能把加速时间混同真实检测延迟。

服务返回建议 `{status, observation_id, reason_code}`，status 为 accepted_new / accepted_duplicate / conflict / invalid / retryable_failure。格式/内容冲突是永久结果；存储不可用为可重试，不返回 accepted，不静默丢。只有持久成功/已存在才可清待发任务；事务已提交但回应丢失时按同键查明或同键重试。QoS 1/PUBACK 只是 broker 接受，不是数据库成功。

鉴权失败放有大小/数量/保留期上限的拒绝审计，勿无限保留恶意载荷。payload 大小、接收队列容量、保留期由 A1 在实现时给出配置和溢出验收，未实现前不称持久可靠队列。

## 2. A → B：设备事件与控制引用

接受 B 提议的 `receive_device_event(event) -> ReceiveResult`，由 A 持久事件/待投递任务后调用 B 服务。字段 source_event_id、device_id、warehouse_id（可 null）、occurred_at、kind、reason；reason 最长 256 字符。kind=offline/recovered/fault 与 C 数据候选的 kind 不共用一个枚举。

source_event_id 为 A 首次状态转换生成并保存的 UUID，重试沿用；连续超时不重复建离线事件；recovered 必须关联原离线事件 `related_source_event_id`（建议新增，请 B 确认）。未知 never_seen 不直接标正常；offline 的判断基于已核对的 last_seen_at 和配置阈值。未绑定设备可以有设备事件，但没有虚构 batch_ids。

建议统一接收结果：accepted_new / accepted_duplicate / invalid / conflict / retryable_failure，返回稳定对象 ID 和 reason_code。同 ID 同内容重复不再通知；同 ID 改内容为 conflict。B 原提案只有三种结果，无法区分数据库暂不可用与永久输入错误，请补充而非吞异常统一 invalid。accepted_new 仅表示事件/告警持久化，不表示两通知成功。重试预算耗尽须留下失败和人工恢复入口。

控制由 A 持有唯一记录；B 只读引用 command_id，不独立更新控制状态。建议 `get_command(command_id)` 供告警追溯使用；创建命令必须现有管理员鉴权且设备为执行器、有效绑定。命令与可选告警引用检查真实存在及同仓库，不接受任意跨仓库 ID。

字段 command_id、device_id、warehouse_id、action、parameters、issued_at、expires_at、status、event_id、ack_received_at、failure_reason。首轮 action=set_cooling，parameters={enabled:boolean}；不直接支持任意 shell/设备参数。完整状态 queued→sent→acknowledged/failed/timeout；过期不能执行；晚到 ACK 留审计，不把已超时自动改成功。

ACK 包含 schema_version、command_id、device_id、executed_at、result（executed/failed）、state、reason。凭据/topic/command/device 匹配、未过期且真的执行才 acknowledged；Broker 回执、仅接收到命令、网页 Toast 不作执行 ACK。同 command_id 重发仿真器不重复执行，但可重发原 ACK；控制后后续温度/功率应实际变化，另行验收。

## 3. A/集成方对 C 查询与 CSV 的答复

以下是 RC1 具体建议，请 C 回答可实现性，B 回答仓库访问范围；不是已开放查询。

- 时间窗采用 UTC `[sampled_from,sampled_to)`，from<to；等价 UTC 规范化。无效参数 422。另支持 received_from/received_to 同样半开区间，两种时间过滤同时提供时 AND，不擅自替换采样时间。
- sampled_at 非法原文不丢，数据库规范列为 NULL；无采样时间过滤时可按质量找到。采样范围过滤排除 NULL；用户显式接收时间过滤可检索它。
- 升序 `(sampled_at NULLS LAST, observation_id)`；NULL 分组按 observation_id；received 排序另用 `(received_at,observation_id)`，不能混用一个游标。cursor 包含契约版本、规范筛选摘要、排序模式、首请求接收水位和末键；签名防篡改，不作授权。筛选/排序/权限版本改变、过期（建议15分钟）或坏签名 422，要求重新首页，不能静默回第一页。
- 首请求固定接收水位，以同样水位核对全量 ID、分页和 CSV；后来到达的历史样本不能插进当前遍历。尚未测试跨请求并发写入，必须补验收。
- limit 默认20、1–100；CSV 忽略页面 limit/cursor，应用同规范 filters/排序/水位和权限。建议最大10000行，超限 422 提醒缩范围，不截断却宣称全量。流式写出 UTF-8（BOM），空结果仍有列头。
- 沿用 A01 最小角色：登录活跃普通用户可读、管理员写。当前未实现逐仓库 ACL，不伪称隔离；若 B 提供仓库 ACL，查询与 CSV 必须共用同一授权函数。沿用当前认证依赖：未登录401、无效令牌403、禁用用户400；禁止对象访问统一404（防存在性泄露）。分页/导出/计数都在授权范围内。此前禁用用户403是提议，和当前实现不一致，已在第7节纠正。
- 建议列序：observation_id,device_id,warehouse_id,sampled_at,received_at,raw_temperature_value,raw_temperature_unit,temperature_c,raw_humidity_value,raw_humidity_unit,humidity_percent,quality,risk_state,rule_version。原始完整 JSON 另通过有权限详情可查，不塞无界字符串。未知值空字符串，quality 以 `|` 分隔、不是把 unknown 写 normal。非法原始采样时间通过详情查，不覆盖规范 sampled_at。
- CSV 用标准库 csv writer 处理引号/换行；来自外部的文本若去前导空白后以 =,+,-,@ 或控制字符开头，加单引号作 spreadsheet 安全输出，保留 raw 在详情；真正验证过的数值列（包括负温）按数值导出。CSV 防注入、空值、中文、换行、负数和极值需自动测试。

## 4. 需要 B/C 明确回复的项（不可替他们确认）

1. C：schema_version 1.0、无顺序 metrics/重复 key、UTC 去重、raw 与 reception、事务结果；查询 NULL/水位/游标/CSV 建议。现有 draft-1 样例不能冒充正式版本，按差异增样例。
2. B：快照原签名返回 list，但“无规则”例子返回 object，必须统一。建议返回 `{rules:[],status:"no_rule"}` 或 `{rules:[...],status:"configured"}`；明确异常/参数和权限。不要在业务中把 list 和 dict 混用。
3. B：快照增加 effective_from/to、immutable rule_version、stored_at/out_at；以 sampled_at 匹配半开区间，历史引用不被新规则改写。同仓多个批次分别匹配，无规则为 unknown；重叠冲突配置应拒绝，不任选一条。
4. B/C：temperature > max（等号不超）与恢复 <= recovery_max、需要的持续时间/连续样本数量由版本化规则明确，不借 C 的8°C fixture 当农艺标准。恢复需关联原事件；无数据/无规则不自动恢复。
5. B/C：首轮候选 kind 统一建议 temperature_high / temperature_recovered / data_quality，severity=info/warning/critical；保留 algorithm_version 与 rule_version（两者不必强行只准裸 semver）。C 的 example_only 和 fixture-threshold-1 仅测试值，不能无映射投递正式业务。
6. B：接受 A 新增 related_source_event_id、接收 conflict/retryable_failure、按 source_event_id 查询结果；C 持久 outbox、B 事务幂等，不需要新微服务。两个服务都不能因超时换 ID。

三方在 #5 明确“接受哪些/拒绝哪些/具体替代”并给对应提交后，A 将确认部分写入共同 contracts、增契约测试并发布真实版本。沉默不作同意；本轮不改 B/C 源码、不建业务表/迁移。

## 5. 2026-10-11 对最新 B/C 回复的补答（仍待确认）

已fetch核对B=447ac38a48a853d83b0afe950b11a945b170ff36、C=bc31333db42ead6c07ef152b2b5613a1402e9380、main=9db01387b1c4db2d0ff3f072a199134d7a64643f；阅读#5的C答复6106213988、B修正答复6108448780及两角色STATUS。C已原则接受观测/查询大部分RC1，新增问题如下答复。B提出的替代不能因单方写“冻结”即当三方正式契约。

### C需要的认证后接收封装和资源上限

计划内部服务输入（不是新增匿名HTTP入口）：`{raw_payload: object, raw_text: str, authenticated_device_id: UUID, warehouse_id: UUID, received_at: UTC datetime, reception_id: UUID}`。raw_text保存本次有效UTF-8原文，不记录凭据；raw_payload为解析的对象快照，C不得修改原输入。received_at/reception_id由A每次实际认证接收生成，同一次DB调用重试沿用，设备网络重传可新增reception。首次持久化由C生成observation_id；后续C返回首条ID，不能由A预先伪造已入库ID。C校验raw中设备/仓库与认证封装一致，A/B绑定采用服务核验而非信任载荷。

建议首版有界配置：MQTT单条UTF-8原包最大32768字节、metrics最多16项（包含未知key）、单个key/unit最多64字符。过大、非UTF-8、非法JSON/顶层非对象无法作为正常观测入库，A拒绝并有界审计；合法JSON内错误时间/数值保留raw由C标质量。超过指标/字段限额由C返回invalid，不静默截断变正常。不得将恶意无界文本塞数据库。

A负责身份拒绝审计、接收待投递队列和投递重试；C负责事务/唯一约束、原始/reception/质量与候选outbox。建议A待投递上限1000条/32MiB（二者先到即满）、重试1/2/4/8/16/30秒后30秒封顶、最多10次自动尝试；满或预算耗尽留失败/溢出审计与人工重试，不清待发也不称可靠成功。未完成入库不得冒充页面最新业务值。重传/回应丢失按同键查询或重试，只有持久成功/已存在可清任务。

拒绝审计建议保留7天、最多1000条，优先只记时间/设备声明/主题/字节数/hash/原因，不留凭据或完整攻击包；必要摘要最多256字节。以上均工程配置提案，尚未实现持久队列/清理/限额，A1需覆盖满队列、停库、重启、回应丢失再声明通过；配置不作性能保证。

reason_code建议：成功`stored/duplicate`；内容永久结果`content_conflict/invalid_payload/invalid_schema/invalid_identity/invalid_metrics/payload_too_large`；暂时失败`storage_unavailable/queue_full/internal_error`。未知的内部错误保留审计并返回retryable_failure，不能伪装格式错误。非法采样时间属可存储的质量标记，存储成功仍stored，不与整包结构invalid混淆。A鉴权拒绝另用`device_unknown/device_disabled/device_unbound/identity_mismatch/topic_mismatch`，不调用C正常入口。B也需明确对应候选reason_code和稳定ID，不自行借用上述全部枚举。

### 查询游标、访问范围、CSV用途

首版提议沿A01“全部活跃登录用户可读、管理员写”，不实现逐仓ACL；请B明确确认，UI/材料声明此限制。查询/计数/CSV共用同一授权函数，cursor绑定用户ID及权限版本`role-v1`，每请求复核账号活跃/角色。权限或筛选变更需新首页。

建议使用现有SECRET_KEY经目的隔离派生仅用于`freshlink/telemetry-cursor/v1`的HMAC-SHA256子密钥（不直接复用JWTtoken作cursor），签名包含key_id、用户/权限版本/筛选hash/水位/排序/末键/签发和15分钟到期。初版不保留旧key；SECRET_KEY轮换后旧cursor失效，422提示重新首页，已有登录影响按原认证策略另处理。密钥仅本机，不输出到Issue/样例。C实现与测试篡改/跨用户/过期/轮换，不把签名当授权。

首版CSV用途是人工查看导出，不承诺机器回导或保留文本字节完全不变；原始完整值仍在有权限详情。优先用结构化ID/枚举/规范时间及验证过数值，不把原始完整JSON塞CSV。raw_value如不能确认为有限数值，不放入数值列，空值加quality，原文详情可查。

历史替代提议曾使用可见标记`[text] `；2026-10-12第7节接受C已测试的单引号前缀作为首版人工查看目标，替换此标记提议。两种前缀不再并列作为正式候选，均不能宣称通用安全；真实验证负数仍是数值。目标表格软件打开/重存须实测，见[OWASP](https://owasp.org/www-community/attacks/CSV_Injection)。

### B新方案还需纠正的冲突

- list统一方向可接受，但需单一完整JSON：空list无规则、条目包含batch/warehouse/入出库、rule_version、UTC生效区间、source/阈值/恢复字段。不同批次可不同版本，不要求所有条目全局共用一个version_id。不要对字符串拼接做含糊“签名”；先明确规范JSON/hash算法及含义，它不是授权或密钥签名。
- 禁止UPDATE/DELETE与更新旧版is_current/superseded_by矛盾。建议不变规则内容+独立可更新current指针或追加版本链接；历史按采样时刻及已引用rule_version读取，不能只查current重算旧告警。生效时间用UTC带时区、半开区间，数据库宜tstzrange而不是无时区tsrange；如何关闭区间/拒绝重叠需给真实测试样例。
- `temperature_high→threshold_high`等kind变更未确认；同一枚举需区分temperature/humidity，不能只根据阈值方向丢metric。A设备事件仍offline/recovered/fault。恢复需唯一新source_event_id加related_source_event_id引用原事件，不能直接把恢复source_event_id设原离线ID导致幂等吞恢复。
- 接收conflict/retryable_failure及by-source查询方向同意；`accepted_new`必须事务持久且不等于通知成功，冲突证据不覆盖原事件。缺规则由B快照明确表示、C判unknown，不归A替业务决定。
- 路径沿v4：仓库在warehouse、告警在alerts，前端features/warehouse及features/alerts；Alembic真实路径backend/app/alembic/versions。由A在backend/app/api/main.py现有api_router挂模块router，不能照不同评论多层重复/api/v1。B提供模块自己的routes及测试后再接，不导入不存在模块。
- 不建议对已推共享分支随意rebase再要求强推。#4合并后先核查祖先和diff，优先安全merge main；需要整洁重建时另建新分支只保本人提交，留旧分支/PR证据，团队确认后使用。基线/迁移只按已合并当前head。

## 6. 接线完成的测试门槛

同一对象 ID 串起真实 MQTT→C 入库→候选→B 告警→两种实收→A 执行 ACK→后续数据→追溯/CSV；同键重发/冲突、队列或 DB 故障、重启、乱序/迟到、断心跳/恢复、过期/晚 ACK、无规则/坏单位/空结果、普通用户和未登录、跨页/全量一致分别实测。固定 JSON 和 Mock 只证明边界建议或界面，不替代 E1。

## 7. 2026-10-12：对 C 的 S0 签收与集成缺口答复

已读取[C 的完整答复](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6125196948)，其签收证据基于 C `9014c0bd5710799fac8013bf3abe90339de08599`。本轮又获取 C `44d962642eeca7298cb484556a58c2eaf2b64903` 的未知扩展字段重发修复：这属于 C 纯处理，不能冒充入库。A 接受下列自己提供/集成的目标；B 的 F02/F03/F04/F05、绑定政策和 F06 权限仍须由 B 确认。

### F01：A/C 已确认的接收目标

A 接受 C 对原始封装、32层容器、32768字节、最多16个metrics及key/unit最多64字符的答复。A检查字节/JSON/容器深度/身份；C检查合法JSON的指标数量/字段限额及质量，越限返回invalid，禁止截断为正常。当前A身份边界实现和C纯处理实现仍各有独立范围。

同一(device_id,message_id)的内容等价涵盖原包未知顶层扩展字段，成员顺序不改变含义；除约定忽略的received_at外，扩展内容改变也属于conflict。metrics顺序与等价UTC表达可规范化；每次可信接收的received_at/reception_id独立，原文始终保存。此项收敛C新修复与A提案，不声称生产唯一约束/outbox已经实现。

### F06：目标服务、HTTP路径与结果

A接受C的候选内部签名 `query_observations(actor, filters, limit=20, cursor=None) -> ObservationPage` 与 `export_observations_csv(actor, filters, received_watermark) -> CSVStream`。actor由现有CurrentUser依赖生成，不能从请求参数选择另一个用户。查询、计数、详情和CSV共用授权及规范筛选；生产代码由C实现。

| 操作 | 单一目标HTTP路径 | 最小行为 |
|---|---|---|
| 查询/分页 | GET `/api/v1/telemetry/observations/` | items、同筛选/水位的total、next_cursor、received_watermark；空结果items=[]、total=0、next_cursor=null |
| CSV | GET `/api/v1/telemetry/observations/export.csv` | 同filters/排序及必填received_watermark；不接页面limit/cursor；先确认行数，再输出UTF-8 BOM/QUOTE_ALL的CSV |
| 受权原始详情 | GET `/api/v1/telemetry/observations/{observation_id}` | 有权存在200；不存在/无权404；raw不进入无界CSV |

C模块router的相对prefix为`/telemetry/observations`，由A在api/main.py挂一次；`/api/v1`只由app/main.py的API_V1_STR添加。`/export.csv`静态路由先于`/{observation_id}`注册。以上路径尚未挂载；不会把内存preview函数直接公开。

筛选沿既有device_id、warehouse_id、sampled_from/to、received_from/to、quality与排序目标；UTC半开区间、AND、NULLS LAST与固定接收水位保持不变。首查询由C服务器确定received_watermark并返回；CSV必须带相同水位和规范筛选。无时区/未来水位或from>=to返回422。详细DTO及非空ObservationPublic字段由C写入自己的提案供核对，不由A假称已有DTO。

| 情形 | 目标HTTP结果 |
|---|---|
| 未携带Bearer凭据 | 401，复用当前OAuth2依赖 |
| 无效令牌 | 403，detail="Could not validate credentials"，复用当前get_current_user |
| 禁用用户 | 400，detail="Inactive user"，复用当前get_current_user；不另改公共认证 |
| 无权对象/不存在 | 404，统一受权详情错误 |
| 参数类型/UUID/UTC区间无效 | 422，普通参数保留FastAPI验证detail列表 |
| 游标坏签名/过期/用户或权限/筛选/排序不匹配 | 422，detail.reason_code="invalid_cursor"，要求重开首页 |
| CSV缺水位或超过10000行 | 422，reason_code分别received_watermark_required/export_row_limit；不截断冒充全量 |
| 存储暂不可用 | 503，detail.reason_code="storage_unavailable"，Retry-After:30；不输出成功空数据 |

这些是待实现的HTTP目标；400/401/403的当前公共认证行为已读源码核对，原第3节的禁用403已纠正。首次角色范围仍等B接受；不得把签名游标视为用户授权。

### 游标密钥与失效

使用服务器现有settings.SECRET_KEY；HMAC-SHA256(key=SECRET_KEY的UTF-8字节,message=`freshlink/telemetry-cursor/v1`)派生子密钥，再签规范JSON载荷。JSON规范为键排序、紧凑分隔符、UTF-8；令牌为base64url(payload).base64url(signature)，验证用恒定时间比较。key_id采用非秘密标签`telemetry-cursor-v1`，不输出密钥或真实游标到公开日志。

载荷至少含contract_version、key_id、actor_id、实际角色与权限政策版本`role-v1`、规范filters摘要、sort、received_watermark、last_key（可空sampled_at＋observation_id）、issued_at/expires_at；15分钟有效。服务每请求重新核对账号/角色；角色变更、跨用户、过滤变更、坏签名和过期统一invalid_cursor。首次不保留旧key；SECRET_KEY轮换使旧游标失效，重新首页生成新水位。C负责实现篡改/用户/过期/轮换及跨页并发写入验证。

### 单一CSV文本目标与待签收项

A接受C最新答复及已有预览的**单引号前缀**作为首版人工查看目标，替代第5节旧`[text] `候选。外部文本检测前导空白/控制字符以及半角/全角`= + - @`；危险文本前加单引号，有限数值的负温仍按数字，原文通过受权详情保存。14列/BOM/QUOTE_ALL保持。目标软件实际打开与重存验证仍由C提供；不承诺机器无损回导或所有表格软件安全。

可读空结果/错误向量见[集成方F06候选样例](../../../contracts/examples/a-query-integration-rc1.json)。本轮只确认A的路径/签名/错误配置和接受C的候选行为；C需确认page字段与HTTP路径，B需确认权限/仓库政策。S0-05待三方接受、样例检查及共同合入后才满足。

## 8. C补签后的结果集一致性修正（待C/B接受）

已读[C的6126476107补签](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6126476107)：C接受第7节F01/F06的路径、page、认证与展示目标，但指出received_at水位不能排除首次查询时未提交、稍后才提交的旧数据。此意见成立，第7节“仅凭固定时间水位保证集合一致”不再作为足够实现方案。

A选择**固定服务端快照**的候选方案，不选择静默最终一致；仍须C实现方和B权限方确认，不冒充已冻结：

- 首查询在一个短事务中，以同一PostgreSQL可见快照生成受权、规范filters与sort下的有序observation/version成员列表并持久保存snapshot_id、actor/role/policy、watermark、创建/过期时间。事务完成后分页，不维持跨HTTP长事务。
- 接收水位仍用于筛选与展示，但不单独标识集合。page额外返回snapshot_id；游标绑定它。新查询不带snapshot时建立新集合；后续页只读该集合，晚提交的旧received_at数据不进入。
- 首版集合上限10000条，与CSV上限一致；超过先返回422 snapshot_row_limit并要求收窄条件，不截断。空集也返回有效snapshot_id。快照TTL900秒，清理过期记录且限制每人活跃快照资源，具体限额由C提出。
- 观测与规范化版本需不可变；快照关联固定版本。若C允许后续重算/修改，需固定投影版本或保存受控导出投影，不能只留ID然后读取可变最新结果。总数来自快照成员；CSV使用完全相同成员/排序/投影。
- 新签名：`export_observations_csv(actor, filters, received_watermark, *, snapshot_id) -> CSVStream`。查询签名不变；分页通过已签cursor携带snapshot_id。CSV必须带snapshot_id与配对watermark，不再接受“仅时间相同就当同一集合”。
- 每请求重新鉴权。用户/角色/政策或对象权限变化导致旧snapshot无效，422 invalid_snapshot并要求重开首页；不能为了固定集合忽略撤权。过期/伪造/跨用户/filters或watermark失配同为invalid_snapshot；缺snapshot为snapshot_required；存储故障仍503。这些条件下不保证旧集合继续可访问。
- 并发验收必须包含：事务T1持有早received_at且未提交，首次查询创建S，T1随后提交；S的后续页、total与CSV均不包含新提交对象，新首页快照才包含。另测并发正常新数据、空集、10001条、到期、撤权、跨用户与同筛选跨快照混用。

此增量只规定可实现目标，正式快照表/服务/游标由C所有；B须接受首版对象权限政策。F01/F06其他已接受条款不重开无关问题。第7节样例是旧候选，新增一致性候选见`contracts/examples/a-query-snapshot-rc1.json`。
