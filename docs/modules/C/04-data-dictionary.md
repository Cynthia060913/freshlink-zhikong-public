# C 固定样例数据字典与来源

当前入口（2026-10-20）：[新库 C PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
`7565fbd2863e6ff4d9bfe1ac1d798506f89c8390`。下方旧 SHA 和“尚无跨重启幂等/CSV 字节流”等句子是**固定纯函数样例的历史描述**；现有 C
隔离候选另有事务 observation/reception、持久快照、CSV 与恢复证据。正式迁移、公共挂载、生产 PG 页面、真实 MQTT/E1 仍未完成。该文件只为七条人工样例定义字段与来源，不把候选代码、CI 绿灯或样例当作真实采集数据。

状态：C4 数据章节的可复核草稿，依据 C 分支提交 `eb469d0304e7e18beb306c673514a4521751f3fa`。本文件只描述[七条固定合成观测](examples/telemetry-contract-proposal.json)及[预验收向量](examples/query-csv-acceptance.json)。这些记录为接口与失败场景测试手工构造，UUID、时间和温度均是样例；不是设备实采、苹果品质标注、实验数据集或业务数据库记录。不得用它们推断保质期、真实品质概率、节能收益或模型泛化效果。

## 来源和处理链

1. `examples/telemetry-contract-proposal.json#a_to_c.observations` 给出 7 条按固定顺序的原始 JSON。另有 `examples/telemetry-input.json` 与 `examples/telemetry-output.json` 作为基础纯处理输入/输出对照。
2. `backend/app/modules/telemetry/quality.py:process_batch` 对每条输入做深拷贝，输出 `raw`、`quality`、`duplicate`、`normalized` 和 `risk`。批内重复键提案为 `(device_id, message_id)`；跨调用/重启的数据库幂等尚未实现。
3. `backend/app/modules/telemetry/query_preview.py:select_preview_observations` 仅在内存中按提案筛选已处理结果；样例分配的 `observation_id` 只用于预验收测试。没有入库、稳定排序/游标、CSV 字节流、鉴权或页面。
4. 固定样例规则是 `fixture_threshold_c=8`，候选条件为有效温度严格大于 8 °C。它不是 B 的版本化正式规则；等号、批次、生效区间及恢复语义仍须由 B/三方确认。原始高温保留；没有实时流插补或错误单位自动换算。

## 字段与单位

| 字段 | 样例含义 | 校验或当前处理 |
| --- | --- | --- |
| `schema_version` | 观测格式版本字符串 | 非空字符串；允许的正式版本尚待 A 确认 |
| `device_id`、`warehouse_id`、`message_id` | 设备、冷库、消息身份 | UUID 字符串；样例 ID 非真实对象。设备鉴权和仓库归属由 A 提供 |
| `sampled_at` | 设备采样时刻 | UTC ISO 8601 的 `Z` 或 `+00:00`；无效值仍留在 `raw`，不得伪装成有效时刻 |
| `received_at` | 服务接收时刻 | 同样要求 UTC；重发时可变化，接收方和最终语义待 A 确认 |
| `metrics[]` | `key/value/unit` 数组 | 当前识别 `temperature` 的 °C 与 `humidity` 的 %RH；温度不设“高温脏值”上限，湿度有效范围为 0–100 %RH；其他指标保留在 `raw` |
| `raw` | 原始观测深拷贝 | 包含缺失、冲突或错误单位的输入，不因质量失败而删掉 |
| `normalized` | 已通过格式和单位校验的值 | 不补造缺失温度，不把 °F 猜测换算为 °C |
| `quality` | 一条或多条质量标记 | 完整有效为 `ok`；缺失、无效、重复及冲突使用具体标记 |
| `duplicate` | `new` / `duplicate` / `conflict` / `unkeyed` | 当前只在一次 `process_batch` 内识别，不代表持久幂等 |
| `risk.state` | `normal` / `candidate` / `unknown` | 缺测、无规则、无效时间、重发或冲突不能填成正常；candidate 不是 B 已存告警 |

质量标记具体包括 `missing_field:<field>`、`missing_metric:<key>`、`missing_value:<key>`、`invalid_id:<field>`、`invalid_time:<field>`、`invalid_unit:<key>`、`invalid_value:<key>`、`invalid_range:humidity`、`duplicate_message` 和 `conflicting_message`；畸形 `metrics` 另有 `invalid_metrics`、`invalid_metric_entry`、`invalid_metric_key` 与 `duplicate_metric:<key>`。正式 API 的质量字段版本仍待确认。

## 七条样例的可核对计数

| 原始顺序 | 场景 | 质量标记 | 批内重复状态 | 风险状态 |
| --- | --- | --- | --- | --- |
| 0 | 正常温湿度 | `ok` | `new` | `normal` |
| 1 | 同键、接收时刻变化且采样 UTC 写法等价 | `duplicate_message` | `duplicate` | `unknown` |
| 2 | 同键、温度内容冲突 | `conflicting_message` | `conflict` | `unknown` |
| 3 | 独立消息、11 °C 真实升温 | `ok` | `new` | `candidate` |
| 4 | 温度值缺失 | `missing_value:temperature` | `new` | `unknown` |
| 5 | 采样时刻格式非法 | `invalid_time:sampled_at` | `new` | `unknown` |
| 6 | 温度值为 104，但单位是 °F | `invalid_unit:temperature` | `new` | `unknown` |

合计：`raw` 7 条均保留；`new` 5、`duplicate` 1、`conflict` 1；`normal` 1、`candidate` 1、`unknown` 5。`ok` 2 条中有一条是高温候选，不能把 `ok` 质量误读成“低风险”。错误单位的 104 不作为摄氏温度比较；非法采样时间的 12 °C 留存但不能在缺乏可追溯时刻时被当作正常观测。

## 复现证据与尚缺数据

固定序列、raw/质量/重复/风险期望由 `backend/tests/modules/test_telemetry_quality.py` 和 `backend/tests/modules/test_telemetry_query_preview.py` 检查。对应提交的 [Backend CI](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38117016730) 在 GitHub Actions Ubuntu/CPython 3.14.8 为 84 passed（C 两份测试共 26 项）、项目覆盖率 92%。这不是 C 本机复现；本机 Git/Python/Docker 和项目启动尚未检查。

后续真实数据章节必须补充合法设备/冷库与批次版本、采集或仿真生成规则、采样频率/时区、样本量、缺测和标注来源、保留策略、脱敏方式、数据版本及训练/验证/测试划分。只有真实采集或获准仿真实验完成后，才能填对应数量与测量结果；模型比较需记录随机种子、独立情境切分和误报/漏报/检测延迟。当前没有模型训练、真实品质标签、数据库持久化、CSV 文件或 E1 实际联调结果。
