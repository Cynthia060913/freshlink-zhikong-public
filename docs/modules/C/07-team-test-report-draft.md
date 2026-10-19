# 鲜链智控团队测试报告底稿（C 汇编）

初版核对日期：2026-10-17；最新补证：2026-10-19。**状态：M03 底稿，未签收**。本文件汇集三人已公开的版本化证据，不能把不同 PR/机器/数据库的结果相加，不能代 A/B 签字。最终报告须在同一发布 SHA、部署配置和第二台电脑上复核后重写实际结果；本文件不证明 E1 或比赛验收。

## 10 月 19 日 C 代码实测头补证（优先于下方旧快照）

| 来源 | 已核实的版本和结果 | 限制与下一次实测 |
| --- | --- | --- |
| A 对修复后 C 的同版组合 | [A 在 E1 #6 的原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6265029865)：A `c15fe46663410fa4112a2767b503bef356814508` + B `3f43af70a29bfc0859fed711333cd38e7e505a05` + C **代码头** `df232b4ce2ada48fce266ed04fcf1071525d86ce`，隔离 Python3.14.8/PG18.6、Uvicorn/JWT/真实 B HTTP **33/33 通过**；此前摘要首失败解除，C 可按 B 回读确认摘要并持久标记 delivered。同精确 C 代码头遥测 **166/166、0 跳过**，其中 4 项严格 PG 专项实际运行。 | A 代跑并提供私有日志 SHA256；不是 C 本人 Windows PG 测试。使用候选 C DDL，没有正式 C Alembic、该探针内 MQTT/连续恢复调度、两通知或完整 E1；手工合成恢复不等于自动三样本恢复。后续文档头 `0dca6f2…` 仅改报告，业务树未改。 |
| B01 正式迁移候选 | B [PR #10 新 HEAD `8561ce1ec0ea4e996bccbdc5f46d473bb170fc09`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) 增 `b01_alerts_storage`（`down_revision=a03_control_storage`）及 9 项迁移测试；[同头 Backend run 39238020710](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39238020710) 的 PG prestart 实际运行 a03→b01，整仓 596 passed。A 在 [PR #10 的独立记录](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#issuecomment-6265683392) 进一步报告精确 A+B856+Cdf 的 PG/HTTP 40/40、B 表由 Alembic 创建、PG 规则快照 UPDATE/DELETE 被拒。 | B01 仍在 draft PR，尚未合 main；C 仍用候选 DDL。C 已对新 B01 测试入口提交[需要修改的审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5572604584)：B 步测试须容纳合法 C 后继，PG 写入测试须限定专用测试库；正式 C 迁移待 B 修复审核并发布唯一 head。 |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) | 分支 `feature/c-data-quality-bootstrap`，本地/远端完整 HEAD `df232b4ce2ada48fce266ed04fcf1071525d86ce`，已包含共同 main `e235a25650fce96b3ec92888e28a3d0702b16a06`，behind 0/ahead 78。本机 Windows/Codex 隔离 Python 3.13：F03 摘要/outbox/B HTTP **合成 39/39**、纯恢复窗口 **10/10**；Ruff、限定 Mypy/Ty、暂存差异检查通过。[同 HEAD Backend Actions run 39234760153](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39234760153) 为整仓 **475 passed/4 skipped**，五项适用工作流均 completed/success。 | 39 与 10 是不同限定测试组，475 是整仓数，不能相加。此头未在 C 本机以 Python 3.14/真实 PG 复测；没有正式 C Alembic、根路由/页面接线、真实 B HTTP、自动恢复投递或 E1。旧 `be795f…` 的 A PG 154/154 只证明旧代码头。 |
| B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) | 当前 B HEAD `3f43af70a29bfc0859fed711333cd38e7e505a05`，C 对该头[源码和回归审查提交 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5569259588)。C 新普通候选摘要对齐其旧行兼容规则：HTTP 正文保留 `related_source_event_id:null`，摘要排除该空字段；非空恢复关联仍计入摘要。 | 审查批准不等于 PR 合并、正式 B 迁移、真实规则联调、告警持久处置或通知实收。B 迁移尚未进入 main，C 正式迁移必须接唯一已合并链。 |
| A 的三模块旧头探针 | [A 在 #7 的实际记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7#issuecomment-6262487834)：A `c15fe46663410fa4112a2767b503bef356814508` + B `3f43af70a29bfc0859fed711333cd38e7e505a05` + 旧 C `be795f248350a24d73a02d7689585009af61e003`，真实 PG18.6/Uvicorn/JWT/HTTP 33 检查中 **32 通过、1 失败**；首失败是普通候选摘要不匹配，C 保持 retry 未伪报送达。 | 当前 C `df232b4…` 已修本地规范化并有固定测试，但 A 尚未在该精确组合重跑；不能把旧 32/33 改写成 33/33。该探针使用候选 DDL，不是正式迁移或完整 E1。 |

新恢复窗口判据以原异常规则显式提供的恢复阈值为前提，要求至少三条不同来源、质量 `ok`、严格有序的采样，单次间隔最多 90 秒；重复/迟到不凑数，缺测/冲突/再异常重置。该纯决策尚未持久化或自动投递，也未将 B 告警置为 `resolved`。C 对当前源树的有限模式安全扫描为 494 个文本文件、9 个二进制未作文本扫描、0 个模式命中；这不是全面凭据审计。下列早前条目保留原来源时间，不作为当前 HEAD 的同版验收。

## 2026-10-19 补证与旧快照边界

下方第 1—3 节是 10 月 17 日的来源快照，表中的旧 main、PR HEAD 和“未签收”判断不能当作今日实时状态。今日已核对的公共 main 为 **`e235a25650fce96b3ec92888e28a3d0702b16a06`**，固定 F01—F06/§3 目标包 `f98e65b4717334bae34f7495181c320ae5061860` 已由三方接受并经 PR #23 合入。C 已取得新版 main 并补本人云端基础启动记录；S0 是否关闭仍由 A 核对三方状态，[共同状态见 #5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5)。这项目标签收不表示正式 B/C 迁移、应用接线或 E1 已完成。

| 10 月 19 日新增证据 | 准确版本与实际结果 | 边界 |
| --- | --- | --- |
| C 本人取得共同基础并执行运行检查 | [main `e235a256…` 的 Actions run #4](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39201471480)：本人 im1yt 手动触发 `published-main`，Ubuntu 24.04/Python 3.14.8/一次性 PostgreSQL 18.6，实际 Uvicorn 健康、匿名 401、密码登录、设备登记 201、重复 409 且计数 1、鉴权列表/详情均通过；临时资源清理成功。 | 共同基础 API 验证；不是 C 正式业务入库/页面/E1，也不是 Windows 本机验证。 |
| C 分支合入共同基础 | [PR #11 `ca2a70bd385b043d5f136a0305a9d2508461c1d6`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)：正常 merge main，behind 0/ahead 72；五项适用 Actions 完成成功。[Backend run](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39201578872) 收集 467 项，463 passed/4 skipped，含 A→C 边界文件 5 项。 | CI 在项目规定 Python 3.14 环境，不能把整仓测试数写成 C 专项或跨模块 E1。C 本机 Python 3.13 且隔离环境缺 FastAPI，完整收集有两个导入错误；可导入遥测子集 146 项中 140 通过、6 项 PG 跳过。 |
| A 对 C 旧精确头独立 PostgreSQL 复测 | [A 在 #9 的原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6259910875)：精确 C `be795f248350a24d73a02d7689585009af61e003`，Python 3.14.8/PG 18.6 遥测 154/154、0 跳过，测试 schema 剩余 0。 | A 代跑旧 C 源码，不是 C 本人或合并后 `ca2a70…` 的 PG 复测；测试隔离表不等于正式 Alembic。 |

| 补证来源 | 准确版本与实际结果 | 仍不能声称 |
| --- | --- | --- |
| C 本人数据模块 | [PR #11 `07bac16f7b5591f323e4ed09a7d51244c9813d82`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)：Windows/Codex 隔离 Python3.13、临时 SQLite 文件，规则选择/原始观测/outbox/F03 契约/B HTTP 合跑 **41/41 OK**；Ruff、Ty、限定文件 Mypy、暂存文本安全扫描及 10 项安全脚本通过。`c6f78…` 同头五项 Actions 已成功；`07bac…` 的远端最终检查仍须单独回读。 | B 真实 F02 SQLModel/HTTP 调用、正式 C Alembic、PG 同头、页面正式挂载、通知及 E1。此 41 项不与 10/17 旧 102 项相加。 |
| A 对 C 的独立补测 | A 在 [#9 精确 `9b26a781cf5be93f899247d7d190234111344330` 的报告](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6252700886)记录 Python3.14.8/受控 PG18.6 的 C telemetry **145/145**，专属测试库清理完成。 | 不是 C 本人 Windows/当前 `07bac…` 同头 PG 结果，也不是正式迁移或真实 B 接收。 |
| A 设备/控制 | A [PR #18 当前 `cffcf4f3b4a73d83ddf3173986ab5a37b239f88f`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18) 的六个文件与本报告 10/17 已列 `53833…` 的对应 Git blob 完全相同；当前头五项 Actions 成功，C 已[独立批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18#pullrequestreview-5566149541)。A 的 340/96% 与 MQTT 实证仍按其原提交范围引用。 | 当前头并未因此由 C 本人重跑 MQTT/PG，B 真实仓库 lookup、C 正式入库及 E1 未验证。 |
| B 规则/告警 | [PR #10 仍为 `9cf9a5de55d48bf0761655b6f2cda844ec5975f6`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10)。A 在 [#8 独立核验](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6250590489)记录该头后端 462/94%，但旧持久行摘要重放和误恢复仍有 P1，C 对误恢复提交了[审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5563695226)。 | PR 未合 main，不能用 462 项绿色把两项缺陷、正式 B 迁移或两渠道真实收件填为通过。 |

下一轮 C 汇编须补同一发布 SHA 的正式 PG/HTTP/页面/CSV 和 E1 #6 实证；A/B 尚未提供的最终业务证据继续写“待验证”。下方 10/17 表格保留原始时间标签，以便追溯测试版本，不作为当前进度总表。

## 1. 证据版本与环境

| 来源 | 被测版本、环境 | 可核对的实测 | 证据边界 |
| --- | --- | --- | --- |
| A 设备/控制 | [PR #18 源码 `53833a7921fbf700a3f945de449646cdc3d88b2d`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18)，A 本人 Windows + Docker 隔离 Linux/CPython 3.14.8、临时 PostgreSQL 18、Mosquitto | A 本人记录完整后端 **340 passed、0 failure/error、覆盖 96%**；真实 MQTT→命令→仿真器 ACK，重放、进程重启、超时/迟到回执等按 A-T01—A-T09 逐条留证 | 来源是 [A 的原测试摘编](https://github.com/Cynthia060913/freshlink-zhikong-public/blob/701f8db50a10d0d4eef7ceefb6e19aacbae28de7/docs/submission/A-test-record-2026-10-15.md)，该材料在未合并的 PR #19；C 未独立重跑 A 整组。B 仓库为合成 lookup，不含 C 正式入库或两渠道通知 |
| B 规则/告警 | [PR #10 `1e5e005970e5cdeca7fcf6a4210e0857787f85b1`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10)，B 本人隔离项目环境 | B 在 [#8 本人状态](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6162631267) 记录 alerts 套件 **226/226** 和同 HEAD CI 成功；A 独立 220 项复核记录在 PR review | PR #10 仍 draft、未合 main；A 第三轮复审仍列临时批次读取故障、有限后继结束后旧规则复活、severity 枚举三项缺陷。B 本人尚未提供修复后同版通过证据，不能按 226 项宣布 F02/F03 签收；真实双渠道收件未做 |
| C 数据质量/隔离持久候选 | [PR #11 代码 `b54af81533e520cf3c1b7376b99cc93c599743a8`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，C 本人 Windows/Codex 隔离 Python 3.13.9 + SQLite 文件 | 十一组 C-only unittest **102 passed / 4 skipped**（2 PG、2 A ingress 需 3.14）；覆盖高温/缺测/坏单位、幂等与事务、质量 SQL、固定快照/CSV、内部签名游标、隔离候选 outbox 正文冻结/冲突留证/重试租约。同代码 SHA [Backend Actions](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469431) 在 Ubuntu/Python 3.14.8 整仓 **307 passed / 2 PG skipped / 75 warnings**，日志明确列出 outbox 新增 5 项通过；A 实际封装→C 隔离存储 2 项也在 3.14 执行 | SQLite 隔离 metadata 不是正式 Alembic/业务 PG；PG 两项本机及远端均未运行，outbox 不读 B 规则/不调用 B，通用 Compose/Playwright 不测 C 应用查询或 E1。后端 307 是整仓总数，不是 C 测试数 |

团队公开 main 在本次核对时为 `fb6de8408c860b277471cdf1697b72fd34162f38`。上表 A/B/C 的提交均属各自 PR，不是一份已经合并并联调的发行版。A/B 结果按本人原记录引用；C 对其测试环境、原始日志或私有配置没有重新核验。旧私有仓库 SHA 不能替代新库同版证据。

## 2. C 本人可复现的案例记录

所有 C 行的输入均为**合成观测**，不是实际冷库采样。固定输入和预期见 [C 样例](examples/telemetry-input.json)、[输出](examples/telemetry-output.json) 与 [查询/CSV 向量](examples/query-csv-acceptance.json)。本表的“实际”来自上述 C 代码 SHA 在本人隔离环境执行，不代表正式应用。

| ID | 输入/步骤 → 预期 | 实际结果 | 证据 |
| --- | --- | --- | --- |
| C-T01 | 正常、真实高温、缺温、坏单位/时间及乱序观测 → 每条 raw 留存；高温不删除；缺测/坏单位/无规则不判正常 | C 单元测试通过，原始值/质量标记/unknown 风险保持；无 B 规则时高温仍为待评估而非真实告警 | `test_telemetry_quality.py`、`test_telemetry_envelope_preview.py` |
| C-T02 | 同设备+消息重发、同键改正文、同接收 ID 改原文与事务中途故障 → 一条业务观测；冲突留证；写失败整事务回滚可重试 | 隔离 SQLite 文件重开及双线程测试通过；拒绝尝试的 exact raw 另存，首次观测未改 | `test_telemetry_storage_candidate.py` |
| C-T03 | 设备/仓库、UTC 采样/可信接收半开时间窗与质量 tag 交集 → 稳定筛选，无效采样可按质量找回 | 候选 SQL 读取结果与测试向量一致；坏时间行仍是 unknown；同键重发不增质量关联 | `test_telemetry_history_candidate.py`、`test_telemetry_snapshot_candidate.py` |
| C-T04 | 创建固定成员快照后插入更早时间的观测 → 旧页/CSV 成员不变，新快照可见；跨 actor/筛选/政策、过期/篡改游标拒绝 | 隔离 SQLite 文件测试通过；同一快照 14 列 CSV 与列表 ID/行数一致，过期清理不删原始观测，故障时回滚 | `test_telemetry_snapshot_candidate.py`；**真正重叠的 PG 未提交 T1 测试已写但本机跳过** |
| C-T05 | 无规则、缺温、坏单位、重发和冲突 → 不生成 C→B 候选；有效高温使用合成规则与外部分配 ID → JSON 保留规则/观测证据 | 固定候选单测通过；没有 B 端接收或投递 | `test_telemetry_candidate_contract_proposal.py`、[候选 JSON](examples/candidate-f03-rc2-c-proposal.json) |
| C-T06 | 实际调用 A 的 `receive_telemetry` 生成封装，再写 C 隔离 SQLite 文件 → 保留可信时间和 exact raw，重投仅一业务观测，冲突不覆盖；坏时间/单位不进入评估 | 本人 3.13.9 明确跳过 2 项；[同代码 SHA 的远端 Python 3.14.8](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39040332878) 日志列出 `test_telemetry_a_storage_boundary.py ..`，两项实跑通过。没有 MQTT、正式 PG 或应用接线 | `test_telemetry_a_storage_boundary.py` |
| C-T07 | 外部分配的异常来源 ID 与规范正文入隔离 outbox → 同正文重发一次、改正文留冲突证据且不覆盖；旧租约不能完成新任务；重开文件后同 ID/正文可重试，永久拒绝不再认领 | 本人 SQLite 文件 5 项通过；注入冲突审计写故障不会伪报成功；[Python 3.14.8 后端 CI](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469431) 日志列出新文件 `.....`。没有 B 接收、by-source 回查、正式 PG 并发或 worker | `test_telemetry_outbox_candidate.py` |
| C-T08 | 原温度异常与显式恢复规则；三条同仓/批/设备且质量 `ok` 的严格有序采样 → 窗口具备冻结资格；2 条、91 秒上限、重复/迟到、缺测/冲突/再异常 → 不错误恢复 | 当前 C `df232b4ce2ada48fce266ed04fcf1071525d86ce` 本机纯窗口测试 **10/10**；未写入正式 PG 或自动发 B，不能声称告警已恢复 | [`test_telemetry_recovery_window_candidate.py`](https://github.com/Cynthia060913/freshlink-zhikong-public/blob/df232b4ce2ada48fce266ed04fcf1071525d86ce/backend/tests/modules/test_telemetry_recovery_window_candidate.py) |

复现范围：在项目隔离 Python 3.13.9 环境，从 `backend` 明确加载十一组 C 单测，含 `test_telemetry_query_preview.py`、`test_telemetry_csv_preview.py`、`test_telemetry_storage_postgres_candidate.py`、`test_telemetry_a_storage_boundary.py` 和 `test_telemetry_outbox_candidate.py`；本人实际执行总计 106 项，其中 2 项 PG 专项因未启用精确测试库、2 项 A ingress 因需 Python 3.14 而跳过。一次通配收集额外导入 A 的 3.14 语法，在本机 3.13 收集失败，未计入通过；明确清单重跑通过。项目 Python 3.14.8 的同版后端总数与 A ingress/outbox 实际执行见上表 Actions。C 测试不会连接或清空真实数据库；不得以 `metadata.create_all` 建生产表。

## 3. 必选闭环未测试清单与下一次采证

| 门禁 | 当前状态 | 后续应取得的同版证据和提供方 |
| --- | --- | --- |
| S0 同版契约与唯一迁移 | **10/17 历史状态；10/19 固定目标已签，C 本人云端共同基础已启动** | A/B/C 已在 [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5) 接受同一 f98 包，PR #23 已合入 A03；C 本人 [run #4](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39201471480) 仅覆盖共同基础 HTTP/API，B 本人环境状态及三方 S0 最终核验仍按 #5；B/C 正式迁移和 E1 另验 |
| A→C 真正接收及 PostgreSQL 入库/重启 | **未测** | A 提供可信 ingress 与启动版本；C 在已合并迁移链上实跑坏包、高温、重发、冲突、断电/重启保留、事务失败及本人 PG opt-in 用例，保留脱敏对象 ID/日志摘要 |
| 鉴权历史、页面与正式 CSV | **未测** | C/A 对同一登录用户、规范筛选和固定快照比对页面/下载 ID、行数、UTC、质量及原值；测试无权、空结果、上限、过期、晚提交、公式文本在目标表格软件中的人工查看 |
| C→B 候选、可靠重试与恢复 | **未测** | B 给同版规则选择与按来源回读；C/B 测同 ID 重放、改正文冲突、超时查无原正文重试、缺测/无规则不发、恢复引用和失败留证；当前合成 JSON 不算收件 |
| 两条通知真实收件、人工处置/设备回执 | **未测** | B/接收人先确认渠道与发送授权，再分别提供端到端脱敏实收证据；A/B 提供控制执行/回执与状态变化；Mock、SMTP 接受或两个邮箱不能填“实收两渠道” |
| [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6) 一库一测点一执行器 | **未完成** | 三人同版运行设备→C 存查→B 告警/真实通知→A 控制 ACK→恢复与历史 CSV，逐段记录时间、对象 ID、失败/修复/复测，不能用各 PR 单测拼成链路 |
| 两库三批十四设备、第二电脑、材料包 | **未测/未签收** | A/B/C 在同一发布 SHA 与可复制配置下完整验证并于 [#12](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/12) 签收 M01—M05；10 月 30 日及之前实际提交要求以团队最新记录为准 |

可选算法 M06—M08 目前没有实际业务调用和独立实验结果，故本报告**不填写**品质概率、货架期、节能率或模型准确率。C 的质量处理/阈值候选不等于真实品质预测。下一次更新应先刷新 #5/#6/#7/#8/#9/#12 与各 PR 的完整 SHA、CI 和本人原记录，再把新的同版实测写入对应行；未提供证据继续标“未测”。
