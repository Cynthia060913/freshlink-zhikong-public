# C 数据处理与验证章节草稿

状态：2026-10-12 C4 模块素材，供团队 M03 软件设计文档选用；**不是**完整系统验收报告、真实采集统计或 M06 模型性能报告。正式发布时以同一实际发布 SHA 复核代码、截图、表格和本文。对应主任务 [#9](https://github.com/Cynthia060913/freshlink-zhikong/issues/9)、草稿 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong/pull/11)。

2026-10-15 新库续作注记：以上旧库链接只作历史证据；当前开发入口为 `Cynthia060913/freshlink-zhikong-public`。本轮受检 C 代码提交 `ba2eeae560cf75aacd81f7f28bde515ecdbe7143` 的五组纯处理测试共 63 项通过，新增超过微秒精度时间的无损失败边界；尚未上传、无同版新库 CI。完整运行条件与缺口见 `03-test-evidence.md` 最新节。下文较早测试数和“本机未检查”叙述保留为当时记录，不代表本轮实际状态。

## 数据对象与来源

项目目标为认证设备经 A 的接收边界上报仓储温湿度。A [PR #14](https://github.com/Cynthia060913/freshlink-zhikong/pull/14) 给出可信设备、绑定仓库、UTC 接收时间和 reception ID 的内部封装提案；该 PR 尚未合并，C 尚未接真实 MQTT/数据库。C 当前 `envelope_preview.py` 对该结构作独立纯处理投影：原始 UTF-8 文本与 JSON payload 快照分开保留；设备自报的接收时间不覆盖服务器可信时间；处理视图另补可信仓库和 UTC 接收时间。身份/仓库冲突显式失败。原文留存是将来审计与复核的前提，不能用清洗值反向覆盖。

当前可执行固定样例在 `docs/modules/C/examples/telemetry-contract-proposal.json`：7 条**人工构造**的正常、同键重发、同键内容冲突、真实升温、温度缺值、非法采样时刻和错误单位观测。另有 `telemetry-rc1-proposal.json` 及 A 接收封装样例，仍属草案。样例阈值 8 °C 只为测试严格 `>` 边界，不是苹果保鲜标准，也不是实际 B 规则。样例数量不能写成真实采集样本量。

## 质量处理与边界

`quality.py::process_batch` 保存每条输入深拷贝，再产生 normalized、quality、duplicate 和 risk。设备/仓库/消息 ID 使用 UUID；采样和服务时间要求带 UTC 的 ISO 8601，`Z` 与 `+00:00` 等价。首轮识别温度 °C、湿度 %RH；单位错误不猜测转换，非法数值/时间保留在 raw 并标质量。高温不设“脏数据”上限，也不因为湿度缺失而抹掉明确的温度超阈候选；缺温、无规则和重发/冲突均不能呈现为正常。重复键以设备 ID＋消息 ID 为提案，同键接收时间变化可重发，内容变化为冲突；当前状态仅限一次纯函数调用，**没有跨重启事务幂等**。

`query_preview.py` 在固定内存结果上按设备、仓库、质量、采样/接收 UTC 半开区间筛选，两个时间窗同时出现取交集；非法采样时刻可在无采样范围时按质量找回。排序按有效采样时间升序、同刻按观测 ID，非法/缺采样时间末尾；续页标记是样例 ID，**不是**签名生产游标或数据库快照。`csv_preview.py` 复用筛选与排序，按 C 在 [S0 F06](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6118778985) 接受的有界 14 列目标写 UTF-8 BOM、全列引用的 CSV；空结果只有表头，超过 10000 行拒绝。外来公式样文本加单引号、负温数值保持数值、重复指标歧义不任选一个值。完整原文留在证据/未来受权详情，不塞无界字符串进 CSV。当前没有 actor 鉴权、数据库接收水位、正式导出路由或页面。

## 已有测试证据

最近代码提交 [`c9ff123ef5c7bacafc8108b0deedee7cc59cc1e1`](https://github.com/Cynthia060913/freshlink-zhikong/commit/c9ff123ef5c7bacafc8108b0deedee7cc59cc1e1) 的 [GitHub Actions Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715585) 在 Ubuntu/CPython 为 **129 passed、75 warnings、项目总覆盖率 93%**；pre-commit、Zizmor、通用 Compose 和 Playwright 在该 SHA 均 success。该数字属于整仓测试，不能写成 129 项 C 测试。C 的固定向量测试实际覆盖原始保留、正常/高温/缺测、重发/冲突、时间/单位失败、envelope 可信字段隔离、同筛选分页/CSV、空结果、超限、负温与公式样文本。后续只改 C 文档的提交 `41786af3afc061315dc77e74f67955023156ebf3` 的 Backend 仍为 129 passed/93%，但共享登录 Playwright 用例出现重试后通过的 flaky 失败；证据见 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6119189499)。不能称该 HEAD 全绿。

C 本机 Windows 项目、Docker/数据库启动、登录/设备页、MQTT→C 入库、B 持久告警、权限导出、跨重启和 E1 **均未实测**；当前 Codex shell 报 `helper_unknown_error: setup refresh had errors`。固定测试复现入口见 `03-test-evidence.md`，执行者须记录机器、完整 SHA、命令和输出，不沿用 CI 冒称本机通过。

## 交付前必须补入的实际结果

| 资料 | 当前状态 | 需要的实证 |
| --- | --- | --- |
| 观测/reception/outbox 数据模型与迁移 | 未实现 | 在已合并 A01→B 唯一 Alembic head 上执行空库升级、事务/唯一约束及跨重启重发/冲突测试 |
| 实时/历史页面及权限 CSV | 未实现 | 同 actor、同筛选、同接收水位核对页/导出 ID、行数、单位、空值、失败码和大于 10000 行拒绝 |
| B 历史规则和异常/恢复候选 | 未实现 | 真实版本、采样时刻、批次、来源 ID、持久 outbox/超时重试和 B 回执；缺规则/缺测保持未知 |
| MQTT 与 E1 | 未执行 | 一库一测点一执行器的真实消息、入库、告警、通知、控制 ACK 和后续数据证据，按 #6 留记录 |
| 累计暴露或可选模型 | 未开始 | 必选闭环后才定公式/单位/训练划分/独立实验/系统调用；无真实结果不填品质概率、货架期或节能率 |
| 跨机与团队测试报告 | 未完成 | A/B 各自交本人测试证据，C 核版本和限制后汇编；另一电脑同版实际复现 |

本章只描述 C 当前真实贡献和缺口。最终 M03 的架构、ER 与完整业务结果由团队合并后的同一版本核对；若采用算法，M06—M08 需另附真实代码调用路径、生成规则、样本量、原始输出和局限，不从此固定阈值样例推断模型效果。
