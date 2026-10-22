# C 测试证据与后续复现清单

## 2026-10-22 C 观测同事务入口（未接生产）

`ingest_observation_on_connection_candidate` 复用现有原文/质量/回执写入逻辑，
由调用方 Connection 决定提交或回滚；Engine 独立入口原有重试边界保留。
C 本机隔离 Python 3.13.9 以 `unittest` 实跑
`test_telemetry_storage_candidate` **13/13通过**，其中三项新增用例验证
原始高温与质量/评估/回执同事务回滚或提交，以及 invalid 和 reception ID
异文审计的整组回滚，以及数据库故障向外抛出后整组回滚。`ruff check`
三个改动 Python 文件通过。
严格 PG 文件共 7 项，本机未启用受控 `test-db`，运行时 **7项跳过**；
新第7项已编写，在真实 PG 两连接上验证提交前不可见、外层回滚无残留和
提交后持久回读，**尚未实际 PG 通过**。这不是 A 门禁已挂载、正式 C 迁移、
容器重启或 E1 的证据。A 之前 A31 的 6/6 实跑只对应旧六项，不能外推到新增项。

C 对 [PR44 精确 a35a1e3 的正式 review](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/44#pullrequestreview-5607772122)
为共同基础候选审核证据，A04 DDL 函数与冻结候选 AST 一致，适用云端七项
success；不等于 C 本机 PG/浏览器实测。

## 2026-10-21 最新页风险过期修复与 A31 核验（M03 未签收）

- C `frontend/src/features/telemetry/TelemetryPage.tsx` 以前只在最新观测 HTTP
  返回时比较服务端 `checked_at` 与可信 `received_at`。轮询暂停后该年龄不会增长，
  可能继续展示旧的正常风险。现以服务端年龄加上成功响应到本次渲染的时间，
  每秒更新；超过 60 秒、无显式 UTC/非法时间、超出 5 秒未来偏差或本地时间
  回拨时显示 `风险未知`。Node24
  `node --test frontend/unit/telemetry-freshness.test.mjs` 实跑 **4/4 通过**，
  覆盖轮询停后过期、无效/非 UTC/未来时间和本地回拨。本机缺 Bun 和
  前端依赖，未在本机跑 Vite/TypeScript/浏览器；待本提交 CI 回读后补精确头。
- A [PR43 `a5f27e6ffe7d56844aa388dbedef2ba2a06ee9ee`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43)
  的 [A31 报告](https://github.com/Cynthia060913/freshlink-zhikong-public/blob/a5f27e6ffe7d56844aa388dbedef2ba2a06ee9ee/docs/modules/A/31-current-c-postgres-followup.md)
  已在 A 受控 Python3.14.8/PG18.6 READ COMMITTED 环境把 C 第 6 项 PG 测试
  **6/6 实跑、0 跳过**；全应用 mypy 93 文件与 ty 通过。C 本人没有在这台
  Windows 机器运行 PG。PR43 还报告 A24 前沿与同组合真实采集/页面/24 行 CSV，
  C 仅对该精确头的默认未启用候选 [正式复审批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43#pullrequestreview-5598813842)，
  不据此签收正式迁移、自动恢复、两通知或 E1。

## 2026-10-21 C 类型返修及 A/B 并发交接复审（M03 未签收）

- C [PR #11 精确 `bf991a0c610dd91cc89be950af15ae020f711bd5`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)：
  本人先在隔离 Python3.13 对旧 outbox 文件重现 mypy strict
  `no-any-return`，再对 PostgreSQL SQLSTATE/pgcode 做字符串类型保护。
  修后该文件 mypy strict、指定隔离解释器的 ty、Ruff lint/format 通过，
  outbox 与恢复 **26/26** 单测通过；未改正常唯一冲突分类。整应用本机
  mypy 因项目 Python3.14 语法在 `app/api/deps.py:36` 停止，不能称全应用通过。
  [本头 Backend](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39543885351)
  **557 passed/6 skipped/115 warnings/93%**，五项适用 Actions success；
  新第 6 项 PG 仍在跳过组，等待受控环境实跑。
- B 在 [#8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6307252259)
  明确交接并保护 ACK 部分提交；A [PR #42 精确 `6ed501f13fd668412ea999b618a97ba188f7db1e`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42)
  保留 B 原提交，补 ACK/恢复同一原异常行锁。A 受控 PG 的 3 个交错均安全且
  实际等待锁，隔离全后端 **1071 passed/0 skipped**；该头普通 CI 后端
  **1065 passed/6 skipped**。C [正式复审批准此交接范围](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42#pullrequestreview-5594935429)，
  本人未本机重跑 A 的 PG/Chromium。PR42 尚未合 main，C 自动恢复仍未发布。
  B PR10 新飞书头有独立审查待修，不可用本批准外推两通知或 E1。

## 2026-10-21 A/C 同版实测与恢复事务 PG 回归补件（未签收）

- A [PR #40 精确头 `e69094ff2c2bf81790a7402befdd8b18066d7c3c`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40)
  普通合入 C `f895e95b1df51c2fa8943be6dbaaa99bbe9e32b1`，保留 C 本人
  模块源码；A 修共享根审计挂载和 latest 路径计数。A 隔离 Python3.14.8、
  PostgreSQL18.6/READ COMMITTED 完整复测 **1058 passed、0 skipped、93%**；
  C 原有 5 项 PG 专项实际运行通过，含通用同连接 outbox 重复/冲突、外层回滚、
  新连接回读与重试。A 原始环境、JUnit 摘要和清理范围见
  `docs/modules/A/27-current-c-transaction-port-validation.md`；PR40 的适用
  Actions 已完成成功。C [按精确头复审](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40#pullrequestreview-5594554218)
  仅批准该 A/C 接线候选范围，未由本人重跑 A 的 PG 或新浏览器全链。
- 本轮新增严格 opt-in 第 6 项：**恢复候选本身**在 PostgreSQL 调用方同一连接
  内冻结，重复重入，外层回滚后由新连接确认原异常仍待投递且无恢复候选，
  最后重新提交并回读唯一恢复候选。沿用随机 schema 和非 CASCADE 清理。
  本人 Windows 隔离环境只能运行 SQLite 代码路径预检 **1/1** 与既有恢复
  测试 **18/18**；PG 专项 **6/6 skipped**，真实 PostgreSQL 的这项新增用例
  仍待隔离环境执行，不能把 A 前述 5 项结果外推给第 6 项。
- 即使第 6 项 PG 通过，也只证明恢复 stage 与调用方事务绑定；A 设备接纳锁、
  连续终态前沿、晚到失效和 B 告警 ACK/恢复共同锁仍未闭环。正式 C 迁移、
  当前同版浏览器链、自动恢复和 E1 仍分开验收。

## 2026-10-21 同连接恢复冻结端口（M03 增量，未签收）

- C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  业务提交 `6e2c1de71fb71c3bb67c9731a311c6d1729c62d8`，严格 PG 专项测试
  后继 `3dd61a3291420e8435cae55b90619a86a463f06c`。候选
  `stage_eligible_recovery_on_connection_candidate` 在调用者已打开的一个
  `Connection`/事务内核对已存证据、原异常和窗口并冻结 F03 outbox；重复/冲突
  使用 SAVEPOINT，不提交外层事务。独立 Engine 包装没有 A 设备锁，不能据此
  启用自动恢复。A 接纳前沿、晚到失效和 B 更正语义仍待跨模块确认。
- 本人 Windows 项目隔离 Python3.13/SQLite 的 outbox 8/8、恢复 outbox
  18/18、delivery 18/18、进度 4/4、规则 7/7，共 **55/55**；Ruff lint/format、
  语法编译、差异检查通过。回滚测试证明原事件送达状态与新恢复候选随调用方
  事务撤销，冲突记录也不提前提交。本机严格 opt-in PG **5/5 skipped**，
  没有连接或清空 PostgreSQL。
- 精确业务头项目 Python3.14
  [Backend run39483084679](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39483084679)
  **557 passed/4 skipped/115 warnings**；精确 PG 测试头
  [Backend run39483603059](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39483603059)
  **557 passed/5 skipped/115 warnings**。两头各自适用的 pre-commit、Compose、
  Playwright、Zizmor 均 completed/success。新增第五项仍被正确跳过，
  **没有真实 PG 回滚测试通过证据**；A 已在 [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6299384034)
  收到精确入口和复验要求。正式唯一迁移、A 锁/前沿、B 并发修复和 E1
  不因本端口或全绿通用 CI 获得验收。

## 2026-10-21 最新已存观测候选（M03 增量，未签收）

- C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  功能提交 `f0e31c0ca01362009903b8a18166720236eb62cb`，Biome 排版修复后精确
  HEAD `9a121597a18293798ad63dc10b8e98287492c84b`。有范围、鉴权的候选
  `GET /telemetry/observations/latest` 按可信首次接收 UTC 取一条已存观测；
  重复投递不替换最新值，轮询不创建历史快照。C 页面每 10 秒重读已提交的
  设备/冷库范围；空、失败、超过 60 秒或时钟不一致显示风险未知，仍不能推断
  其他设备或批次正常。历史/CSV 沿用原固定快照。
- 本人 Windows 项目隔离 Python3.13 的历史服务单测 **4/4 OK**，Ruff lint/format、
  语法编译和 Git diff 检查通过。HTTP 用例因项目 Python3.14 语法在本机未跑。
  精确修复头项目 Python3.14
  [Backend run39480816473](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39480816473)
  **554 passed/4 skipped/115 warnings**；pre-commit、Compose、
  [Playwright run39480816789](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39480816789)、
  Zizmor 同头 completed/success。初始 f0 功能头的 pre-commit 因两处前端
  排版失败，已由 9a 修复，不能把初始头记为全绿。现有 Playwright 用例并未
  验证此候选页面交互；4 skipped 不计正式 C PG 测试。
- 尚未验证：正式迁移/重启、共同根与导航接线、同版真实浏览器刷新和权限、
  多设备/多批次业务风险、自动恢复、两渠道实收、完整 E1 与 M03 成品。

## 2026-10-21 C 拒收审计与详情风险提示（M03 底稿，未签收）

- 本人 C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  精确业务头 `077cbef46a04155a7b2b1535e2ac5985af9fb423`，main
  `75c1e86b984b8241bab1ecd5790a89225809d306`。先前 `48a6f6ea1eb2fbfb5fc20c8753d70ea07c79ee0b`
  提供**未挂主程序**的管理员专用拒收审计候选：可信设备/仓 UUID、最多 50 条、
  UUID 游标、`Cache-Control: no-store`，能读取没有形成 observation 的 invalid、
  消息冲突及接收 ID 异文复用的精确原文。测试包括匿名/普通/停用/管理员权限、
  限额、游标、原设备和后来声称方都可查复用记录。UUID 游标不是时间顺序或冻结
  快照，新增行可能排在已有游标前；须从头审计，不能用空页证明环境正常。
- `077cbef…` 进一步在普通观测详情只显示与原观测接收 ID 相关的**复用次数**
  与风险警告，不暴露后来声称方的精确原文。HTTP 回归验证跨设备复用后原观测
  raw/既有关联接收列表不变，计数从 0 变 1。孤立 invalid 不会出现在普通详情。
- 本人 Windows 项目隔离 Python3.13 仅完成 Ruff lint/format、语法编译和 Git
  差异检查；因项目 API 依赖 Python3.14 语法，新增 HTTP 用例未在本机运行。
  精确 `48a6f6e…` [Backend run39475822611](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39475822611)
  **552 passed/4 skipped/112 warnings**；精确 `077cbef…`
  [Backend run39477013822](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39477013822)
  **552 passed/4 skipped/113 warnings**，两头各自适用的 pre-commit、Compose、
  Playwright、Zizmor 完成成功。Playwright 通用用例未覆盖新增详情交互；4 skipped
  不能视为正式 C PostgreSQL/迁移通过。
- C 对 A [PR #39 精确共享接线 COMMENT review](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/39#pullrequestreview-5589912256)
  只读核对其 A-only 十文件、B 模块文件与 B `0f107…` 一致；Node 语法/diff
  检查通过。A 自行提供的 PG/浏览器证据未由 C 本机重跑；B PR #10 的 ACK/恢复
  并发 P1 由 C [REQUEST_CHANGES](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5586852909)
  和 A 实际 PG 复现，绿灯/共享页面不解除此阻断。
- 待验：正式 B01→A04→C 唯一链、C 表在 PG 空库升级及重启保留、冷库级
  原文权限、主程序审计/详情页面与 CSV 同版、自动恢复锁与提交前沿、B 两独立
  终端实收及完整 E1。上述不同版本的结果不能合并为一次系统验收。

## 2026-10-20 晚间精确头补证（M03 底稿，未签收）

- C 最新业务代码 `7565fbd2863e6ff4d9bfe1ac1d798506f89c8390` 的跨设备/冷库
  接收 ID 重用保守阻断回归，本人 Windows 项目隔离 Python3.13/SQLite
  **39/39 OK**；项目 Python3.14 [Backend run39428737160](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39428737160)
  **550 passed/4 skipped/108 warnings**。后续文档头
  `e6bb8ef25974cdc6c709811c6424302caa68cb99` 的 [run39429549594](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39429549594)
  同为 550/4/108。4 skipped 不计作 C 正式 PG。
- C HTTP 筛选回归代码头 `4860d328650170907113f35de8ac9a3d496b58bd` 的
  [Backend run39427662361](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39427662361)
  **549 passed/4 skipped**，只证明该候选应用源码被项目 Python3.14 测试；
  C 本人 Windows 未安装项目 Python3.14/Docker，未运行实际浏览器/PG。
- A 对精确 C `e6bb8ef25974cdc6c709811c6424302caa68cb99` 的
  [独立 PG 增量复测](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6292845397)：
  受控 PG18.6 独立 schema 中四项坏证据防护和一项真实 JWT/SQLModel
  TestClient HTTP 筛选回归通过，0 失败/错误/跳过，C 业务源码/断言未改。
  这是 A 的五项增量和其清理记录；首次工具配置失败也保留，不代替 C 本人
  PG、本头全套测试、实际浏览器或正式 Alembic。
- A [PR #38 精确头 `6a804c972fed16dfa67d496015116b9f7753e420`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/38)
  四文件已由 C [源码复审](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/38#pullrequestreview-5586477440)。
  本人从该精确 Git 树通过 `git show <SHA>:scripts/check-live-control-browser.cjs | node - --csv-self-test`
  实际得到 `PASS 4 CSV parser and 2 cooling-baseline regression cases`；只运行自测，
  没有在 C 本机重跑 A 的 PG/MQTT/Chromium 完整探针。A 在 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6292497922)
  报告的 47 条浏览器快照/CSV 同序属于其精确运行 `884fd66786f0f7560074e1630db3e41101e57a6a`、
  C 旧 e8/B 旧 856 候选，不外推本 C 新头。
- B PR #10 的 `470566fb9b840f7a172fc1fa7eac1ca0321615a2` 安全返修由 C
  [精确头复审](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5586394948)，
  [远端 Backend run39436721712](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39436721712)
  **644 passed/0 skipped/80 warnings**；本人未重跑真实 PG。共享 CI 文件仍由 A 审核，
  正式链与两种独立渠道真实收件均未完成。

三组证据的源码组合、运行环境不同，不能合并为同版 E1 通过。具体矩阵见
[团队测试报告底稿](07-team-test-report-draft.md)。

## 2026-10-20 已存坏接收证据保守阻断（M03 底稿，未签收）

- C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  业务头 `6110f3b3c611d6790fd1eb8be29dfbcf38a1fc0b`，基于共同 main
  `75c1e86b984b8241bab1ecd5790a89225809d306`。本轮只改 C 的三个服务文件、
  一个测试和 integration-notes；不改公共路由或正式迁移。
- 对已进入 C 持久接收表但未形成新观测的 `invalid/conflict`，以及同
  `reception_id` 异文重用，按解析后的可信 UTC 接收时间核对。原异常以后的
  同设备/仓坏证据使扫描及冻结返回 `held_invalid_evidence`，不产生恢复 outbox；
  原始接收/冲突记录保留，风险不判正常。三项新增回归覆盖扫描前坏接收、
  窗口已 eligible 后晚到冲突、ID 异文重用。早于原异常的坏证据不在本窗口内。
- 本人 Windows 项目隔离 Python3.13+临时 SQLite 文件，恢复相关
  **38/38 OK**；Ruff lint/format、Git 暂存差异检查通过。精确头远端项目
  Python3.14 [Backend run39423643885](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39423643885)
  **548 passed/4 skipped/103 warnings**；pre-commit、Compose、Playwright、Zizmor
  同头 completed/success。4 skipped 不算 C 正式 PG。
- A 对 C 旧 `e8a08e62b5902f75ece8195ef44d740fc8995abb` 独立复测
  [192/192 遥测、3/3 新扫描真实 PG 替换 fixture](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6287388032)，
  均为 A 的隔离环境和旧 C 头，不能外推为本 `6110f3b` PG 通过。
  C 对 A [PR #36 精确头批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/36#pullrequestreview-5583810362)
  仅限同设备未结队列顺序；对 [PR #37 评论复核](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/37#pullrequestreview-5585082483)
  确认 11 个 C 文件与旧 e8 字节一致，新 611 未进入其组合验证。
- 尚未验证：A 队列中的未提交前驱、检查/冻结竞态、跨 worker 提交前沿、正式
  C PG/迁移、自动恢复、真实 B resolved、完整 E1。缺这几项时不得把上述
  合成三好样本或同头 CI 宣称自动恢复安全；最小接口提案见
  [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6291022753)。

## 2026-10-20 已存观测单窗口扫描（M03 底稿，未签收）

- C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  业务提交 `d36a0aa54eee5a263c12e0a4025e1f412d63197d` 加显式单窗口扫描，
  `78b98d8e700124ee55c7941813ec9e66557e3255` 仅按项目 Ruff 格式修正。
  下节 `15c87…` 是当时的业务代码头，不应再称当前头。共同 main
  `75c1e86b984b8241bab1ecd5790a89225809d306` 已安全纳入该分支。
- 从已存观测按 A 可信接收时间+观测 UUID 有界分页，使用持久窗口水位在连接重开
  后续扫；非法设备时间保留原值、质量未知，重置一次，三条新有效样本才合格。
  缺历史规则等待、缺候选表可重试且不隐式建表。此端口仅**显式调用**，不安装
  生产调度，不提供跨进程锁或 A inbox 晚提交补偿；合格不等于 B 告警已恢复。
- 本人 Windows/Codex 隔离 Python3.13+SQLite 文件的恢复/outbox/扫描测试
  **13/13 OK**，新增三项真实执行；Ruff lint/format、修改文件语法和 diff
  检查通过。首业务提交 `d36a…` 的远端 pre-commit 因 Ruff 排版失败，
  `78b98…` 已修；不把旧红灯擦除或冒称本机真实 PG。
- 精确 `78b98…` [Backend run39390566196](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39390566196)
  在项目 Python3.14 整仓 **545 passed/4 skipped/103 warnings**；五项 Actions
  均 completed/success。四项跳过不算新扫描同头 PG 专项，通用浏览器与容器
  工作流不算 C 页面挂载或完整 E1。
- A 对上一 C `6af704…` 在隔离 PG 的遥测 **189/189** 和新增接收水位探针
  **5/5**，准确来源见 [A 在 C #9 的记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6285968627)；
  这是 A 代跑的**旧代码头**，不能挪为本扫描端口的 PG 通过。B PR #10
  安全返修与 B01→A04→C 唯一正式迁移仍是本轮应用接线前置。

## 2026-10-20 前一 C 头、可信接收水位与规则故障分类（M03 底稿，未签收）

- 本人 im1yt 的 [Draft PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  当时已上传业务代码头 `15c87c66de0eec2578248ce21ca95fc928c97a8a`，共同 main
  `75c1e86b984b8241bab1ecd5790a89225809d306` 已安全纳入；C 恢复水位增量为
  `644e9dafe56ce05266a7a7494c6727fb0a1950ed`，e4ab 仅修测试文件排版，
  b55f 仅更新两份底稿。15c87 把 B 历史规则查询超时/断线/权限暂不可用
  明确归类为可重试，pending、原始观测和未知风险保持不变，不生成候选。
  旧 `bc71…` 是下节当时证据，不能再称当前 HEAD。
- C 隔离候选恢复进度表持久记录 A 可信接收时间与 observation UUID 的水位。
  非法/缺失设备采样时间仍保留 raw、质量未知，重置窗口一次；SQLite 文件连接
  重开后，旧坏观测重放不再清空后续有效样本。新增一项纯判据和一项隔离数据库
  回归，**尚无正式 C 迁移、持久跨窗口扫描或数据库容器重启实测**。
- 本人 Windows Codex 隔离 Python 3.13 纯处理子集 **97/97 OK**；完整后端依赖、
  Docker 和 PostgreSQL 未在此环境运行。暂存快照 509 文件/9 二进制未文本扫/
  0 发现；文本扫描不是完整安全审计。新增 SQLAlchemy 回归本机缺依赖未运行，
  改动文件仅完成 Python3.13 语法检查与 diff 检查。
- [精确代码头 Backend run 39385744813](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39385744813)
  在项目 Python 3.14 运行整仓 **542 passed / 4 skipped / 103 warnings**；
  新增规则故障回归文件实际被收集执行，pre-commit、Compose、Playwright、Zizmor
  也均完成成功。4 项跳过不算 C 正式 PG；
  通用 Compose/Playwright 不等于 C 页面或 E1。前一 `644e9da…` 的 Ruff 排版
  红灯已在 e4ab 修复，保留历史失败记录。
- B PR #10 仍停在 `2a5575923e765baf4134eabe25e3f73dbcff08be`，安全 PG 返修
  未见新头。A PR #34 的 `41b977170e49b530285d22774b6aae16697592c3`
  是未激活六表迁移候选，远端 Backend 1 failed/989 passed/4 skipped，
  C 已提交[修改请求](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/34#pullrequestreview-5581672494)。
  C 在 [#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6285216749)
  接受候选唯一顺序 B01→A04→C 十表后继；尚无 C01 活动迁移或完整 E1。

## 2026-10-20 最新 C 精确头（M03 底稿，未签收）

- C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  已推送 `bc71b76368c69952657c318297452482f49881c0`，基于共同 main
  `e235a25650fce96b3ec92888e28a3d0702b16a06`。新独立端口将已持久的
  连续恢复窗口与 C F03 outbox 连接：核对原异常本地 delivered、原时刻的
  历史规则、每条恢复观测质量/时间/身份后，冻结稳定来源 ID 的恢复正文；
  重开 SQLite 文件重复调用不多一条事件，规则不符、缺失或污染观测阻断。
  入口目前仍需调用方显式触发，不是正式自动恢复。
- 本人 Windows/Codex 隔离 Python 3.13 + SQLite 文件库，相关 C 存储、
  恢复、F03、outbox、投递和规则 **71/71 OK**；新模块 Ruff lint/format、
  Mypy strict、Ty 和暂存文本安全扫描通过。扩展通配收集共 173 项，含 2 项
  导入错误和 6 项环境跳过：本机 3.13 不能解析项目 3.14 的 PEP 758 语法，
  隔离环境也缺 FastAPI；这不是 A 源码缺陷，不把本机通配收集称为通过。
  Docker、psycopg 和真实 PostgreSQL 在本机未安装/未运行。
- 精确 HEAD 五项远端 Actions 均 completed/success；[Backend run
  39283815955](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39283815955)
  在项目 Python 3.14 环境跑整仓 **488 passed / 4 skipped / 103 warnings、
  93% 总覆盖率**。整仓数不是 C 专项数，4 项跳过不算实跑；通用 Compose
  和 Playwright 成功不证明 C 正式 API/页面或完整 E1。
- A 对较早 C `78e61cd347646911b3e6a6c94d0998aa23545af4`
  [受控 PG 实测](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6270680816)
  为遥测 177/177、持久窗口探针 5/5；不是本 `bc71…` 的同头 PG。
  B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10)
  `2a5575923e765baf4134eabe25e3f73dbcff08be` 仍 draft/未合 main，正式 C
  迁移接审核合入后的唯一链；真实 B 恢复、共享主程序、E1、第二机和 M03
  签收均未完成。当前进度以 [C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6263404899)
  为准。

## 2026-10-19 当前 C 证据（M03 底稿，未签收）

- 共同 main `46fa463d6ca4f71965b8db74b08a41f352b05cfb` 已正常合入；
  [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  已普通推送至 `94909d348f852ed4e3ba3e77cd6a33258a8a3c84`。此前本地 HTTP
  适配 `5b9fa2acfb529f479fe5c94374c4e8cb71be6d2f` 和证据提交
  `b67063b199ee9e5137982ab5bc16aa757925a9fe` 均已上传；旧节的“待上传”
  只反映 10 月 18 日当时网络故障。
- 949 同 SHA 的 Backend、Compose、pre-commit、Playwright、Zizmor 五项远端
  Actions 实际均 completed/success。通用浏览器/容器检查没有运行未挂载的 C
  页面、正式 C 迁移、真实 B 候选接收或 E1。
- 本轮 C 恢复回读改动在 Windows/Python3.13 隔离环境执行
  `test_telemetry_b_http_candidate`、`test_telemetry_delivery_candidate`、
  `test_telemetry_outbox_candidate`、`test_telemetry_f03_body_candidate`，
  **35 项 OK**；Ruff、mypy、ty 检查改动模块通过。恢复候选须回读原异常
  `status=resolved` 才标记本地 delivered；回读仍 open 时冻结同正文重试。
  此条是 C 的合成端口/临时 SQLite 测试，未实际调用 B HTTP；本改动提交 SHA、
  有限安全扫描及同头 CI 在 #9/PR11 记录。
- 随后的本轮 C 投递边界复测 **36 项 OK**：旧式或损坏的冻结 F03 正文在
  by-source 查无后由 B HTTP 适配器本地拒绝，outbox 记录
  `invalid_local_candidate` 并转 `rejected`，未向 B 发 POST，后续不再抢租；
  原正文与来源 ID 保留以便人工排查。此项仍仅是合成 HTTP/临时 SQLite，
  没有真实 B 服务或 PG 同头投递。
- A 独立 Python3.14.8/PG18.6 的 C opt-in 3/3 仅对应旧
  `a97b9c04ff9cb3cd32872e87335b11bfadf677e1`，不是当前分支 PG 证据。
  本机完整启动/登录/设备页、正式 PG 迁移、已挂载鉴权页面和 E1 仍未测。

以下 10 月 18 日及更早记录均保留为准确时点的历史证据；实时状态以 PR #11
和 [#9 STATUS C](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019) 为准。

## 2026-10-18 最新 C 证据索引（M03 底稿，未签收）

- **版本**：共同 main `095c3a7b34ceacd4b9d52cbea180d77de596f2dc` 已正常合入
  C 分支；[PR11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
  当前已上传 HEAD `032c2cc700b76902e50502020164b1d9490c6209`。
  本机下一提交 `5b9fa2acfb529f479fe5c94374c4e8cb71be6d2f` 的 B HTTP 适配
  因 GitHub 443 连接重置/超时**尚未上传**；没有把该源码称为团队可获取的远端交付。
- **C 本机模块实跑**：Windows 隔离 Python3.13.9；F03 正文规范化固定普通与
  恢复摘要及 outbox 重开、恢复依赖/投递、合成 B HTTP 回执映射和认证失败，
  相关 34/34 OK。C-only 可运行子集实跑 136 项：131 passed、5 skipped。
  更广的 138 项 telemetry 收集有 2 个导入错误：A ingress 使用项目 Python3.14
  语法，本机只有 3.13；隔离 venv 未装 FastAPI，C HTTP TestClient 无法本机导入。
  这些错误未计入通过；本机 `docker` 命令未找到，未起完整服务/登录/设备页。
- **远端同头工程检查**：已上传 `032c2cc7` 的 Backend、pre-commit、Compose、
  Playwright、Zizmor 五项 Actions 均 completed/success。该通用浏览器检查未
  实际点击未挂载的 C 页面；默认 PG opt-in 不据此声称实跑。后端具体计数以
  [同头工作流](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11/checks)
  日志为准，不把整仓数写作 C 专项数。
- **受控 PostgreSQL 独立证据**：A 用 Python3.14.8/PG18.6/A24 对合并前 C
  准确 `a97b9c04ff9cb3cd32872e87335b11bfadf677e1` 运行三项 PG opt-in，
  **3 passed / 0 failed / 0 skipped**，含 JWT/详情/固定快照CSV、幂等回滚并发重连、
  晚提交隔离，见 [A 在 #9 的证据](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6250624268)。
  旧 `66b9b54` 的 2过1失败是夹具污染历史，不能覆写；新 `032c`/本机 `5b9`
  未跑同头 PG，也未证明正式 C Alembic/应用共享根路由。
- **失败与待证**：B PR10 的新恢复 `9cf9a5d…` 已有 B 模块测试，但 C 在
  [跨模块审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5563695226)
  指出非温度原事件可能被恢复的边界，待 B 修复；C 没有实际把候选发到 B。
  正式持久表与迁移、真实 A→C 运行、已挂载鉴权 HTTP/页面、CSV 浏览器下载、
  B 候选投递/恢复、两通知终端实收、E1、第二电脑复现及14设备完整场景均未验收。
  A/B 自己的证据仍按其本人 #7/#8/#12 和精确 SHA 引用，不能由 C 填成全系统通过。

以下为 2026-10-17 及更早阶段的真实证据，按其各自 SHA 和当时状态保留；其中“本机待上传”或“没有数据库查询”不代表上方 2026-10-18 的隔离候选进度。

## 当前可交接证据索引（2026-10-17）

- 新库 C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 已上传候选代码 [`b54af81533e520cf3c1b7376b99cc93c599743a8`](https://github.com/Cynthia060913/freshlink-zhikong-public/commit/b54af81533e520cf3c1b7376b99cc93c599743a8)，基于 main `fb6de8408c860b277471cdf1697b72fd34162f38`。C 隔离表有事务入库、原文/质量、接收 ID 复用拒绝留证、质量/时间 SQL 筛选、固定成员与投影快照、同快照 14 列 CSV、签名内部游标及过期清理；A 真实 ingress 封装→C 隔离存储边界测试和新增 outbox 正文/租约候选。**没有**正式 Alembic、B 实际收件、生产路由、可信用户鉴权或页面。F03 候选/恢复 JSON 仍是提案。
- 本人 Windows/Codex Python 3.13.9 隔离环境，十一组 C-only `unittest` 实跑 **102 passed / 4 skipped**；2 项必须显式启用精确测试库的 PostgreSQL 回归、2 项 A ingress 项目 Python 3.14 要求均明确跳过。新 outbox 5 项通过；Ruff lint/format、候选模块 mypy strict/ty、暂存有限文本安全检查 441 文件零发现（9 个二进制未按文本扫描）与安全脚本测试 10/10 通过。快照清理和冲突审计各有故障注入；不要把默认跳过写成 PG 通过。
- 同代码 SHA [Backend Actions](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469431) Ubuntu/Python 3.14.8 整仓日志为 **307 passed / 2 PG skipped / 75 warnings**，明确列出新 outbox 文件 5 项及 A ingress→C 隔离存储 2 项通过。该 SHA 的 [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469613)、[Compose](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469471)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469603)、[Playwright](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39041469533) 均 completed/success。通用工程检查不验证 C 正式 PG、HTTP、页面或 E1。
- A 对较早 C 隔离事务版本做的[独立 PG18 与测试容器重启检查](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6195386092)是**A 的外部证据**，未覆盖本次新加的晚提交快照、拒绝审计，也不能代填成本人的 opt-in 测试执行。C 当前运行环境无 Docker/psycopg，本人尚未实跑 PostgreSQL 两项。
- [M03 团队测试报告底稿](07-team-test-report-draft.md) 已按 A/B/C 版本和各自证据汇编；A/B 的完整同版结果仍由本人提供，C 不代填，**未签收最终报告**。正式 A→C 入库、重启、鉴权历史/CSV、B 候选可靠交付、E1、另一电脑复现和真实收件均未完成或未测。实时状态以 [#9 的 STATUS C](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019) 为准。

## C→B 候选正文冻结与持久重试的隔离测试（2026-10-17）

`backend/app/modules/telemetry/outbox_candidate.py` 只在 C 独立候选 metadata 中
增添出站表和冲突证据表；测试用临时 SQLite 文件，不建正式迁移、不调用 B。
`source_event_id`、关联观测 ID 与规范 JSON 正文在入队时冻结；同 ID/同正文
返回重复，改正文返回冲突并单独保留拒绝正文，同一冲突重放不增第二条证据。
到期任务用持久租约标记认领，租约过期或显式可重试失败后仍使用同一来源 ID
与正文；旧租约不能覆盖新认领者的完成状态。永久拒绝留下有限错误码且不再
认领。正文上限 64 KiB 和 30 秒默认租约只是本隔离候选的保护值，未获公共
契约签收。

本机 Windows/Codex Python 3.13.9 的五项新测试实跑通过；连同此前明确的
十组 C-only 清单合计 **106 项：102 passed / 4 skipped**。新增还注入冲突
审计写失败，确认不会伪报成功，原候选仍保持不变。四个 skip 是两项
精确测试库 PostgreSQL opt-in、两项 A ingress 需要 Python 3.14。另一次
通配收集把 `test_telemetry_a_ingress_boundary.py` 直接导入，在本机 3.13
因 A 模块采用项目 3.14 语法而于收集阶段报错；这不是测试通过，随后按明确
的 11 组清单重跑得到上述结果。新文件 Ruff、mypy strict 和指定隔离解释器
的 ty 均通过。**尚无**真实 B 收件、超时后 by-source 回查、PostgreSQL 并发
租约、正式 outbox 迁移/worker、重试次数上限或 E1。不同正文冲突证据的
生产保留期限/容量也待确认。

## A 接收封装→C 隔离存储新增边界用例（远端已执行）

`test_telemetry_a_storage_boundary.py` 增加两项 C 自有测试：实际调用 A
`receive_telemetry` 生成可信封装，再写 C 的临时 SQLite 文件候选表；核对
原文/可信接收时间、重投幂等、改正文冲突、重开库，以及坏采样时刻和错单位
保留原文但不排队评估。A ingress 模块使用项目 Python 3.14 语法，本人机器只有
3.13.9；**这两项在本机明确 skipped**，同代码 SHA 的
[Python 3.14 后端 CI](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39040332878) 实际列出该文件 `..`，两项通过。它仍没有 MQTT broker、正式
PostgreSQL 迁移、应用路由或 E1，不能代替 A→C 正式接线验收。

## C1 双接收并发回归（2026-10-15）

在临时 SQLite 文件数据库，两个线程同时把同一设备/消息、不同可信 `reception_id` 送入 C 独立事务候选。若竞争事务返回 `retryable_failure`，待锁释放后用原接收封装重试；最终一条 `accepted_new`、一条 `accepted_duplicate`，两者引用同一 observation，库中只有一条 observation、两条 reception、一条 evaluation job。Windows/Codex Python 3.13.9 隔离 venv 首次九项存储候选测试 **9/9 OK**，连续执行该存储测试组十遍均通过；六组 C-only 合计 **88/88 OK**。修改的测试文件 Ruff lint/format 通过。此证据只证明隔离 SQLite 的双接收与可重试边界；不证明 PostgreSQL 唯一约束的并发隔离、正式 Alembic、生产 A 接收或应用重启。远端 CI 结果在推送并回读后另记。

## C2 F03 严重程度枚举（2026-10-15）

在[共同 #5 四项答复](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6194380612) 中，C 接受 `info|warning|critical` 为 F03-RC2 目标集合。固定候选投影仅从有效规则复制这些值，其他值或类型显式拒绝；`normal` 不是严重程度，缺测/无规则不能由此伪装为低风险。新增一项回归验证三个接受值和五种拒绝输入。Windows/Codex Python 3.13.9 隔离虚拟环境实际运行六组 C-only `unittest` **87/87 OK**；改动的两个 Python 文件 Ruff lint/format、候选模块 mypy strict 与 ty 均通过。此测试只覆盖 C 内存候选及前述隔离 SQLite 候选，不证明 B DTO 已收窄、正式契约签收、持久 outbox 或真实投递。同版远端 CI 以本提交推送后 GitHub 实际结果为准。

## C1 隔离数据库事务候选（2026-10-15；代码 `7f566b9f13de171b9e268b0e5edb813e8da5c640`）

- `storage_candidate.py::ingest_observation` 在 C 自有、未纳入正式迁移的候选表上将 observation、reception 和 evaluation work 放入同一 SQLAlchemy 事务。可信 A 封装的 exact `raw_text`、原 `raw_payload`、可信设备/仓库/接收时间分别保留；设备/消息 UUID 唯一约束、reception UUID 主键确保新消息、重发、冲突与同一接收调用重试区分。提交前失败回 `retryable_failure` 并回滚；新 observation 的 ID 和待评估任务跨数据库重开保留。结构性坏业务键只留 reception，坏采样时间/单位照实保存为 `unknown`，真实高温值不删除；规则未接入，风险仍 `unknown`。
- Windows/Codex Python 3.13.9 使用**独立临时虚拟环境**，安装项目锁定的 SQLAlchemy 2.0.51、Ruff 0.16.9、mypy 2.3.1、ty 0.0.84；实际运行 7 个新 SQLite 文件数据库事务测试，加原五组 C-only 测试，共 **85/85 OK**。覆盖原文/可信时间隔离、engine 重开后的重发、同接收 ID 幂等、同消息冲突、坏时间/单位/缺温、坏消息 ID、事务中途故障回滚后重试。Ruff lint 通过，格式化后复测通过；新存储模块单文件 mypy strict 和指定隔离解释器的 ty 均通过。该测试使用临时 SQLite 文件，不是 PostgreSQL、应用重启、并发双写或正式业务路由验收；表结构是 C 内部候选，生产不得 `create_all` 或另建平行迁移头。
- 同代码 HEAD 的远端 Actions 已回读：Backend [run 38747430823](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38747430823) 在 GitHub Ubuntu/Python 3.14.8 执行整仓 **288 passed、75 warnings、95% 总覆盖率**，日志列出本文件 7 项测试；[pre-commit](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38747431020)、[Compose](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38747430909)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38747430852)、[Playwright](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38747430799) 均 completed/success。288 是整仓数量；Compose/浏览器是通用检查，不证明 C 已接正式 PG/页面。
- 本轮本机 Python 3.14、Docker/真实 PostgreSQL、A 实际 MQTT 调用、B 历史规则/候选投递、正式鉴权查询/CSV、页面和 E1 均**未测**。

## 新库当前证据索引（2026-10-15）

- 接线说明受检提交 `c08829be8cc4437a34e9f442bf606448b9cfa7ce` 位于 C PR #11 / `feature/c-data-quality-bootstrap`，当时 base main `f7fe62a95eceee75bebba3e135fc67d6c7728643`。该提交仅刷新 C 接线文档；本机 `git diff --cached --check`、有限暂存文本扫描（426 文件零发现、9 个二进制未按文本扫描）及公开安全脚本测试 **10/10 OK**。同 SHA 的后端、Compose、pre-commit、Zizmor、Playwright 五个工作流均 completed/success；通用工程检查不等于 C 业务验收。
- 前一 C **代码**提交 `e0f14c44c19597c925ca5e3ed387d8f01d1796cd` 在 Windows/Codex Python 3.13.9 的五组 C-only `unittest` 实跑 **76/76 OK**；同 SHA 五个工作流均完成且成功。该组仅覆盖固定纯处理、内存筛选/CSV 预览和合成候选，不覆盖 PostgreSQL、授权 HTTP、B 接收、跨重启、MQTT 或 E1。项目 Python 3.14 与 Docker 在这台执行环境不可用，未执行本机完整启动/登录；A→C ingress 模块因本机 3.13 不能解析 A 的 3.14 语法，未计入本机通过。
- [新 #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019) 保持最新版状态；下列各节按自身 SHA 和当时测试解释。A/B 的材料证据仍待各本人提供，M03 不据此签收。

## C1 质量门控补查（2026-10-15；增量提交待记录）

- 隔离事务候选原先只要采样时刻和规范温度存在，就将观测加入规则待评估队列；但原包同时出现非法指标项时，质量处理给出 `invalid_metric_shape` 和 `unknown`。现收紧为仅 `no_threshold_rule`、有效采样时刻与有效温度可排队，非法指标形状仍保留原文/观测但任务为 `held_unknown`，避免后续规则层从不可采信投影制造候选。
- 新增该失败边界回归，Windows/Codex Python 3.13.9 隔离 SQLAlchemy 2.0.51 虚拟环境实跑六组 C-only unittest **86/86 OK**（存储候选 8 项）；Ruff lint/format、新存储单文件 mypy strict 和 ty 均通过。该结果仍只含临时 SQLite 数据库，不是正式 PG、B 规则调用或 E1；对应远端同 SHA 检查在推送后回读。

## C0 乱序到达高温回归（2026-10-15）

- 固定样例先到一条较新的正常观测，再到同设备、不同消息 ID 的较早高温观测。C 纯处理保持两条原始观测和各自 UTC 采样/接收时刻，不把晚到高温误判为重复或删除；后者仍标为高温候选。此测试不建立正式事件时间水位、数据库历史排序或 B 规则版本选择。
- Windows/Codex Python 3.13.9 实跑五组 C-only `unittest`：**78/78 OK**；`git diff --check` 通过。此结果仅证明固定内存输入的处理边界，未测试 PostgreSQL 持久化、跨重启去重、正式查询/CSV、B 接收、页面或 E1；项目 Python 3.14 和同提交远端 CI 仍需按实际结果核对。

## C2 候选质量证据一致性回归（2026-10-15）

- 纯候选投影在输出前用同一原始处理视图和固定阈值重新核对规范值、质量标记与风险记录。原始温度真实升高而湿度缺失时仍保留高温候选，并在证据中保留 `missing_metric:humidity`；若质量被改写成 `ok`，或原始湿度被事后补入但处理结果未更新，则显式拒绝候选。原始对象不被修改。这仍是固定样例的内部防护，**没有**接 B 的规则仓库或正式候选接收端。
- Windows/Codex Python 3.13.9 在 `backend` 目录运行五组 C-only `unittest`：**77/77 OK**，候选模块 **11/11 OK**；`git diff --check` 通过。测试只针对纯内存对象，未运行项目 Python 3.14、PostgreSQL、真实 API/页面、跨重启 outbox 或 E1。同提交远端检查以 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11/checks) 的实际结果为准；此处不提前记为通过。

## C2 原始身份与采样时间追溯回归（2026-10-15）

- 基于已推送 C 分支 `3cc1ac7b1b01b9f531d625abf757c3167ce63907`，纯候选投影补查原始观测与规范投影的 schema、设备、仓库、消息 ID 和采样时刻；任一不一致则拒绝生成候选，不能从被修改的原始身份或时间制造可追溯告警。UTC 等价格式仍由已有有效向量覆盖，原始输入不改写。
- Windows/Codex Python 3.13.9 在 `backend` 目录实际运行五个 C-only `unittest` 模块：**76/76 OK**，其中候选模块 **10/10 OK**。本轮只验证固定内存对象及拒绝边界；PostgreSQL、B 接收、正式 API/页面、重启与 E1 均未测试。该增量完整提交为 `e0f14c44c19597c925ca5e3ed387d8f01d1796cd`，同 SHA 五个远端工作流已回读成功。

## C2 原始温度证据一致性回归（2026-10-15）

- 基于已推送 C 分支 `2dcec80e056f853c78e40d287ea37fb92c179e30`，纯候选投影增加防护：处理结果虽标记 `candidate`，若保留的原始摄氏温度、规范温度或风险记录互相矛盾，显式拒绝生成候选。对应测试分别篡改原始值、风险温度、风险原因，验证不能从不一致证据制造高温告警；原始输入和有效高温样例仍保持不变。
- Windows/Codex Python 3.13.9 实跑五个 C-only `unittest` 模块：**32+11+13+10+9=75/75 OK**；候选模块单独 **9/9 OK**。这是固定内存对象边界，未调用 B 接收端或数据库，也未验证正式告警、重启、权限、页面或 E1。该增量完整提交 `3cc1ac7b1b01b9f531d625abf757c3167ce63907` 已推送；同 HEAD 的 PR #11 页面显示 13 项成功、2 项按配置跳过，不以 CI 代替正式业务验收。

## C2 固定候选纯投影增量（2026-10-15）

- C 自有代码提交 `2aac6e476190c0875d2094b4d1346fa1dd950782` 新增 `candidate_preview.py::build_candidate_preview`。函数消耗已处理高温、外部已分配的 `observation_id`/`source_event_id` 和**合成规则快照**，生成与 `examples/candidate-f03-rc2-c-proposal.json` 一致的单批 JSON；缺规则、缺温、错单位、无规则状态、重发和冲突不发候选。仓库不匹配、规则不在采样时刻生效、单位/阈值不一致或来源 ID 非 UUID 均显式拒绝，原始观测和规则输入不变。它不生成或持久保存来源 ID，也未调用 B 服务。
- Windows/Codex Python 3.13.9 在 `backend` 目录实际运行五个 C-only `unittest` 模块，**74/74 OK**（原 71 项加 3 项构建/不发/错误规则测试）；`git diff --cached --check` 通过。一次尝试把 A→C 导入边界模块加入同一命令，因 A 的 Python 3.14 多异常语法在本机 Python 3.13 解析失败，随后只运行上述五个 C-only 模块；没有修改 A 源码，也不把失败尝试称为通过。项目 Python 3.14、B DTO 实际接收、持久 outbox、跨重启幂等、PostgreSQL、HTTP、页面和 E1 仍未测试。本提交的远端检查须以 PR #11 对应 SHA 的实际运行结果另行记录。

## 新库早期证据与 M03 测试章节底稿（2026-10-15，历史版本）

- 对应 C 业务代码完整提交：`1ee909cf0efe9b29a540ba1e50c99d62285e18a0`，新库 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，基线 main `f7fe62a95eceee75bebba3e135fc67d6c7728643`。本人分支已普通推送，本地/远端/PR HEAD 回读一致；工作树干净、相对 main 落后 0/领先 10。此文档的后续提交仅更新证据文字，不追溯改写已测代码版本。
- 本机环境是 Windows 上的 Codex 工作区、Python 3.13.9；设置 `PYTHONPATH=backend`、`PYTHONDONTWRITEBYTECODE=1` 后，对 `test_telemetry_quality.py`、`test_telemetry_envelope_preview.py`、`test_telemetry_query_preview.py`、`test_telemetry_csv_preview.py`、`test_telemetry_candidate_contract_proposal.py` 分别运行 `python -X utf8 -m unittest discover -s backend/tests/modules -p <文件名> -q`，结果 **32+11+13+10+5=71/71 OK**。项目 Python 3.14 未在这台机器安装；`ruff` 本机不可用。`git diff --check` 通过。
- 固定对象 ID：设备 `11111111-1111-4111-8111-111111111111`、仓库 `22222222-2222-4222-8222-222222222222`、正常消息 `33333333-3333-4333-8333-333333333333`、高温消息 `44444444-4444-4444-8444-444444444444`，均见 [telemetry-input.json](examples/telemetry-input.json)。它们是**合成样例**，不是真实冷库设备记录。

| 本轮记录 | 输入、步骤与预期 | 实际结果与边界 | 可核证据/缺陷复测 |
| --- | --- | --- | --- |
| C0-RAW，2026-10-15 | 将正常、高温、缺测、错单位、重复、冲突及坏时间样例交给纯处理函数；原始值须保留，高温不删除，缺测和无规则保持 unknown | 五组 C 纯处理测试的相关用例通过；本机没有数据库写入或真实 MQTT | `test_telemetry_quality.py`、`test_telemetry_envelope_preview.py`、上述固定 JSON；[C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019) |
| C0-ENVELOPE，2026-10-15 | 固定可信封装中给出 exact `raw_text` 和 `raw_payload`；不一致、重复 JSON 成员、非法常量或坏原文须有界拒绝，原文不得在解析异常中泄露 | `test_telemetry_envelope_preview.py` **11/11 OK**；只测 C 自有封装结构，不证明 A 的生产接收 | 首轮 `9286e23906dc6571b591715091cf90bd452a746c` 的规范检查因 3 个 C 文件格式失败；按日志修复为 `1ee909cf0efe9b29a540ba1e50c99d62285e18a0` 并同版复测 |
| C1-PREVIEW，2026-10-15 | 固定内存观测筛选/排序后生成 14 列 CSV；测试同筛选 ID、空结果、上限、危险文本与孤立代理项处理 | `test_telemetry_query_preview.py` **13/13 OK**，`test_telemetry_csv_preview.py` **10/10 OK**；这是内存预览，不代表正式 HTTP 导出、鉴权或表格软件复测 | 两测试模块和 [query-csv-acceptance.json](examples/query-csv-acceptance.json) |
| C2-CONTRACT，2026-10-15 | 对待审 F03-RC2 的有效单批高温候选与缺温、错单位、无规则、重复、冲突不发向量做固定 JSON 核对 | `test_telemetry_candidate_contract_proposal.py` **5/5 OK**；B 实际 DTO/接收、来源 ID 跨重启未测，接口未冻结 | [候选样例](examples/candidate-f03-rc2-c-proposal.json)、[S0 C 答复](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6181720375) |

同一业务代码 SHA 的远端 GitHub Actions：[后端](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38663735736)、[Compose](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38663735738)、[pre-commit](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38663735748)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38663735676)、[Playwright](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/38663735705) 均 `completed/success`；PR 页面显示 13 项成功、2 项按配置跳过。Compose 和 Playwright 是共享工程检查，**没有**验证 C 的正式数据库、业务页面或 E1。没有手工重跑来掩盖首次 pre-commit 失败。

M03 团队测试报告仍缺 A/B 本人提供的同版运行证据，入口分别为新库 [A #7](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7)、[B #8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8)；C 不代填其通过结果。C 自己还须在已合并的唯一迁移链上实测 PostgreSQL 入库/并发幂等/重启、登录鉴权、历史查询/正式 CSV、B 候选接收与重试、E1、第二电脑复现。到这些真实运行前，M03 测试章节只是底稿，不是最终签收。

## A→C 原文与处理对象一致性（2026-10-15；本机待上传）

- C 的 `prepare_receptions` 在固定纯处理边界校验可信封装的 `raw_text` 与 `raw_payload` 指向同一 JSON 对象；拒绝二者值或类型不一致、重复 JSON 成员、非 JSON 常量、非对象与坏 JSON，不让追溯原文和质量判断指向不同观测。有效高温及已有正常/缺测样例仍照常处理，原文不被改写。
- Windows/Codex Python 3.13.9 本机五组 C-only `unittest discover`：**32+11+13+10+5=71/71 OK**；其中封装模块新增 5 项测试并扩充无效 JSON 子场景。坏原文先于封装的 `raw_payload` 属性访问被有界拒绝，不让解析异常泄露原文。这里使用 C 自有 `ReceptionLike` 固定封装，不证明 A 实际 MQTT 接收、PostgreSQL 入库或跨服务 E1；项目 Python 3.14 与远端同提交 CI 尚未实测。

## 新库 CSV 隐形前缀回归（2026-10-15；本机待上传）

- 基于本机 C 分支 `821fc7090756f40376c6e2f89b30cf4abccc0e96`，修复 CSV 人工查看预览：外来文本先有空白、再有零宽或其他格式字符、最后才是公式前缀时，同样加文本前缀；原始观测保持不变。新增三种此类子场景，仍仅是预览字节处理，**不是**正式 HTTP 导出或权限验证。
- 本 Windows/Codex Python 3.13.9 分别实跑五个 C-only `unittest discover` 模块：**32+6+13+10+5=66/66 OK**。Docker 命令不可用、项目 Python 3.14 未安装；PostgreSQL、服务启动/登录、页面、真实 E1 和本提交远端 CI 均未测。

## 新库 F03-RC2 C 侧固定向量（2026-10-15；本机待上传）

- 基于本地 C 分支 `8832d6d67eec36538a469bf889e8f2fba1c96300`，对照新库 PR #14 `contracts/F03-RC2.md` 的**未签收目标**，新增 `examples/candidate-f03-rc2-c-proposal.json` 与两项纯向量测试。正常样例为单批、有效 UTC 高温、已分配观测/来源/规则快照 UUID；失败向量为缺温、错单位、无规则、同键重复与冲突。原始处理视图保留，不把缺测判正常；同一候选重试沿用原来源 ID 是设计目标，尚无持久 outbox 可实测。
- 本 Windows/Codex Python 3.13.9 运行五个 C-only `unittest discover` 模块：**32+6+13+10+5=66/66 OK**，其中新增两项核对 RC2 顶层字段、批次/仓库/规则/UTC 与纯质量计算，以及五种不发候选的原值/unknown 状态。`git diff --check` 无错误。测试只读取合成 JSON，不调用 B 服务、PostgreSQL、MQTT、HTTP、页面或通知；B PR #10 远端 DTO 仍未接收 RC2，本增量无同 SHA 远端 CI。

## 新库接入后 CSV 边界复测（2026-10-15；本机待上传）

- 基于本机 C 分支 `78a77629bdf454409bfc8ad51beb74d30a571fa4`，只修改 C 的 CSV 预览、对应测试和说明：对 BOM、零宽、双向格式字符开头的外来文本加人工查看前缀；JSON 中孤立代理项在 CSV 视图转义为可见文本，原始观测仍保留。此增量没有接正式 HTTP 导出。
- 当前 Windows/Codex Python 3.13.9 分别运行五个 `test_telemetry_*.py` 独立模块（不包括需 Python 3.14 的 A→C 导入边界）：**32+6+13+10+3=64/64 OK**。CSV 模块单独 10/10 OK；新增代理项回归，格式字符在原有用例增加 3 个子场景。`git diff --check` 无错误。项目 Python 3.14、PostgreSQL、鉴权、页面、真实 CSV 接口和 E1 未测；未取得本增量同 SHA 的远端 CI。
- 邀请已接受，身份 im1yt、网页发帖成功；[新 #1 接入记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/1#issuecomment-6181504937) 和 [新 #9 C 状态](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019) 已发布。Git CLI 安全认证与 Connector 新库选择仍未完成，**本机增量未上传**；远端 PR #11 不含上述本机增量。下方较早的“邀请待接受”记录只描述当时状态。

## 新公开仓库本机复核（2026-10-15；尚未上传）

- 新库 `origin/main` 为 `f7fe62a95eceee75bebba3e135fc67d6c7728643`；本轮受检 C 代码提交 `ba2eeae560cf75aacd81f7f28bde515ecdbe7143` 位于本机 `feature/c-data-quality-bootstrap`，远端 C 分支仍为 `33f9ad84ea0504a5f4614d3ee21ae00d6cc6dd80`。新 main 已安全合入；受检提交相对 main 落后 0、领先 4，PR #11 相对新 main 的文件差异仅含 C 模块、C 测试和 C 说明。新旧仓库 SHA 不可互认，以下旧库链接仅作历史证据。
- Windows 上 Python 3.13.9 设置 `PYTHONPATH=backend`、`PYTHONDONTWRITEBYTECODE=1` 后，分别以 `python -m unittest discover -s backend/tests/modules -p <文件名> -q` 执行 `test_telemetry_quality.py`、`test_telemetry_envelope_preview.py`、`test_telemetry_query_preview.py`、`test_telemetry_csv_preview.py`、`test_telemetry_candidate_contract_proposal.py`：**32+6+13+9+3=63 项通过**。新增一项验证超过六位小数的采样时间保留原文、标无效/未知，且同键不同原文不因 Python 微秒截断而误判重复。A→C 代码边界另依赖项目 Python 3.14，未在此环境完整运行。
- 暂存快照有限文本安全检查 424 文件、零发现，9 个二进制文件未按文本扫描；`git diff --check` 通过。`ruff` 在当前 Python 3.13 环境不可用，未完成规范检查；项目 Python 3.14、PostgreSQL、Docker、MQTT、真实应用启动/登录、权限、重启及 E1 均未测。本轮受检提交未推送，**没有同 SHA 新库 CI**；这些 63 项纯测试不证明正式业务入库、HTTP 查询或 CSV。
- 当时的新库接入阻塞：Connector 身份 `im1yt`，公开源码可读但尚未取得新库写入通道；评论/审查曾返回 `403 Resource not accessible by integration`。其后邀请已接受、网页发帖已成功；当前 Connector 所选仓库仍缺新库，Git 推送未成功。以上提交仅在本机，不能算其他成员可获取的源码交付。

## 最新增量：A→C 边界与 F03 目标样例（2026-10-13）

- C 分支 `feature/c-data-quality-bootstrap` 受检代码为 [`e51215ea50650e81a91892ecb6ebc4ca93f67a31`](https://github.com/Cynthia060913/freshlink-zhikong/commit/e51215ea50650e81a91892ecb6ebc4ca93f67a31)，已包含共同 main `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813`，PR #11 相对 main ahead 50 / behind 0，27 个差异文件均属 C 范围。
- **本 Codex Windows 环境实跑**：Python 3.13.9，在隔离镜像运行 `$env:PYTHONPATH='backend'; python -X utf8 -m unittest discover -s backend/tests/modules -p 'test_telemetry_*.py' -q`，**65/65 OK，0.213 秒**。镜像包含该 C 提交的纯处理/六个单测/固定 JSON，以及共同 main A 的 `iot/ingress.py`；仅为本机 Python 3.13 导入给 A 镜像的一处 Python 3.14 多异常语法加括号，远端 A 文件未改。新增 5 项 A→C 代码边界覆盖可信身份与 UTC 接收时间、真实高温原文、重发/冲突、缺测/错单位/坏采样时刻和身份拒绝。F03 的 3 项仅核目标 JSON，不调用 B 服务。
- **远端分版本结果**：`b2d562bb0d25c3597547aaf63a36b830bd3692a7` 的 [Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38420404039) success；同版 [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38420404086/job/115375907993) 实跑后因两个 C 格式差异失败。最新 `e51215ea…` 修这两处，但其五个 workflow 显示 failure，pre-commit job **0 步骤、日志 404**，不能判定此 HEAD 的代码门禁通过或失败；未手动重跑。本机 Ruff/项目 Python 3.14 未测，尝试在隔离目录安装匹配 Ruff 版本受网络限制而失败。
- **不覆盖的验收**：没有 PostgreSQL 持久化/跨重启幂等、MQTT、B 实际候选接收、鉴权/正式 HTTP 查询或 CSV、应用启动登录、E1。C1 原文/reception/事务/outbox 仍是 [设计草案](06-persistence-transaction-proposal.md)。B 的唯一迁移未合 main，F03 丰富候选与当前 B DTO 差异已写 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6148596048)。

## 当前 C0/C1 边界修复与同 HEAD CI（2026-10-12）

- 受检完整提交 [`9a8d3fff915d784024ecc9fb52bf11818bdd8662`](https://github.com/Cynthia060913/freshlink-zhikong/commit/9a8d3fff915d784024ecc9fb52bf11818bdd8662)，分支 `feature/c-data-quality-bootstrap`、[PR #11](https://github.com/Cynthia060913/freshlink-zhikong/pull/11)，共同 main `a99c2a091204012891b8fa0c80e10b63087c57c1`。GitHub compare 实测 merge base=main、ahead 42 / behind 0，22 差异文件均在 C 模块/测试/`docs/modules/C`；不代表本机工作树已检查。
- [Test Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264617708)：GitHub Actions Ubuntu / CPython 3.14.8，整仓 `coverage run -m pytest tests/`，**133 passed、75 warnings、总覆盖率 93%**；C 新向量覆盖同键未知扩展内容改动判冲突、指标数量/key/unit 形状越界保留 raw 且风险 unknown、高温不被删、全角/半角公式文本单引号前缀、控制字符和负温数值。这里的 133 是整仓计数，未声称均为 C 测试。
- 同 SHA [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264617697)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264617707)、[Test Docker Compose](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264617650)、[Playwright](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264617648) 均 success。Compose/Playwright 是共享工程检查，没有测试 C 的正式入库、页面或 E1。
- **失败与修复不抹去**：中间提交 `57b10f0373589902c2141b34038a73a81a7332ba` 的 [Backend run 38264293575](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38264293575) 为 **1 failed、133 passed**；新 CSV 测试把控制字符写成字面 `\\x00`，期望保护但输入实际以反斜线开头。提交 `9a8d3fff915d784024ecc9fb52bf11818bdd8662` 改为实际控制字符向量后按上列同 HEAD 完整重跑通过；不能把中间运行列成成功。
- **业务边界**：目前 `process_batch`、`prepare_receptions`、`query_preview`、`csv_preview` 是固定数据纯处理/预览。正式 C ingest 的 `invalid` 回执、原始观测/reception 持久事务、跨重启幂等、真实登录权限/数据库分页/HTTP CSV、B 规则和候选 outbox、表格软件打开/重存、Windows 本机与 E1 均**未测试或未实现**。Codex 命令执行器仍报 `helper_unknown_error: setup refresh had errors`，本机 Git/Python/Docker、origin、启动/登录/设备页未检查。F01/F06 候选签收与剩余问题见 [S0 C 回复](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6126476107)。

## 当前 C1 有界 CSV 向量与 CI 证据（2026-10-12）

- 受检 C 代码完整提交 [`c9ff123ef5c7bacafc8108b0deedee7cc59cc1e1`](https://github.com/Cynthia060913/freshlink-zhikong/commit/c9ff123ef5c7bacafc8108b0deedee7cc59cc1e1)，共同 main `a99c2a091204012891b8fa0c80e10b63087c57c1` 已安全合入 C 分支；PR #11 相对 main 只改 C 模块/测试/文档，behind 0。
- [Test Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715585) 在 GitHub Actions Ubuntu/CPython **129 passed、75 warnings、总覆盖率 93%**；新增/调整的 C CSV 测试实际解析 UTF-8 BOM + QUOTE_ALL 字节，核对与分页同筛选 ID、有界 14 列、空结果表头、10000 行拒绝、非法时间/缺温未知、真实高温候选、负温数值不改写、公式样文本前缀与重复指标歧义留空。原始 payload 的完整留存继续由质量处理和 A envelope 测试验证，不用 CSV 替代原始证据。
- [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715696)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715722)、[Test Docker Compose](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715645)、[Playwright](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38211715700) 均 success。Compose/Playwright 是通用工程 CI，不测试 C 业务页面/入库。前一 SHA 43f61ed 的 Playwright flaky 失败记录仍保留在下方与 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6118639527)，不能抹掉。
- 本机 Windows 工作树/origin、Git/Python/Docker、启动/登录/设备页**未检查**，Codex shell 报 `helper_unknown_error: setup refresh had errors`。数据库持久化/迁移/重启、权限/签名游标/正式 CSV、B 历史规则与候选重试、MQTT/E1 仍无实际测试证据。F06 列是 [C 在 #5 接受的 RC1 目标](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6118778985)，不是三方正式冻结。

## 前一 C0/C1 样例与 CI 证据（2026-10-12，代码提交 43f61ed）

- 远程共同 main `a99c2a091204012891b8fa0c80e10b63087c57c1` 已合入 C 分支，最近受检代码提交 [`43f61ed0f243d1dc079029b9d0877f64f9e93f35`](https://github.com/Cynthia060913/freshlink-zhikong/commit/43f61ed0f243d1dc079029b9d0877f64f9e93f35)；PR #11 只含 C 代码、测试和文档。正式接口仍未签收，B 迁移未并入 main。
- [Test Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38209778594) 在 GitHub Actions Ubuntu/CPython 的整仓测试为 **128 passed、75 warnings、项目总覆盖率 93%**。C 的固定样例测试包含正常、高温、缺失、非法时间/单位、批内重发/冲突、原文与可信封装隔离、内存查询分页、CSV 实际字节解析与同筛选 ID 核对、空结果、10000 行拒绝、公式文本和负温数值。此计数是整仓，不冒充 128 项全属于 C。
- [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38209778597)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38209778646)、[Test Docker Compose](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38209778613) 成功。Compose 为现有通用工程检查，未验证 C 业务数据入库。
- [Playwright](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38209778667) **失败**：shard 3 日志为 17 passed、2 flaky；登录错误 token 跳转和重置密码收件均在重试后通过，但 `--fail-on-flaky-tests` 判失败。当前 C 增量只改后端 CSV 测试，没有前端/工作流改动；共享排查请求见 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6118639527)。不跳过门禁，也不称全绿。
- C 本机 Windows 工作树、origin、Git/Python/Docker、启动/登录/设备页均**未检查**；Codex shell 创建进程失败，原文 `helper_unknown_error: setup refresh had errors`。上述 GitHub CI 不等于本机复现或真实业务闭环。

复现固定测试的建议命令：在仓库 `backend` 目录分别运行 `uv run pytest tests/modules/test_telemetry_quality.py tests/modules/test_telemetry_envelope_preview.py tests/modules/test_telemetry_query_preview.py tests/modules/test_telemetry_csv_preview.py`；此命令**没有在 C 本机执行**，执行者须单独记录机器、SHA 与实际输出。数据库跨重启、A→C MQTT、B 规则/候选与通知、授权查询/正式导出、E1 仍须后续独立实测。

## 旧版证据与执行环境（2026-10-11 历史记录）

- C 分支：`feature/c-data-quality-bootstrap`；本证据来自提交 `d8e0a8770404601e73d82a0f65d116a576b8f976` 的 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong/pull/11) 检查。该 PR 基于远程 main `9db01387b1c4db2d0ff3f072a199134d7a64643f`；A01 PR #4 尚未合并。正式接口和迁移仍待三方确认。
- [Test Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38116328057) 在 GitHub Actions Ubuntu 24.04、CPython 3.14.8、pytest 9.1.1 上运行。日志显示 `coverage run -m pytest tests/`，结果 **79 passed、55 warnings**；其中 `tests/modules/test_telemetry_quality.py` 为 **21 项通过**；项目总覆盖率 **92%**，C 的 `quality.py` 报表也是 92%。这是该 PR 的 CI，不是 C 本机执行。
- 同一提交的 [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38116328047)、[Test Docker Compose](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38116328043) 和 [Zizmor](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38116328083) 均成功。Compose 成功只表明当前通用工程启动检查通过，未验证 C 观测入库。
- [Playwright Tests](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38116328042) 失败于 `changes` 作业的 `dorny/paths-filter`，日志原文 `Resource not accessible by integration`；`test-playwright` 被跳过。不能记录为浏览器测试通过，也不能归因于 C 页面行为。
- Codex 本地命令执行器返回 `helper_unknown_error: setup refresh had errors`。本机 Git/Python/Docker、origin、工作区未提交文件、启动、登录和本机单测均**未检查**；未改动本机工作区。

## 已执行的 C 纯处理场景

下表的“通过”仅指上述 GitHub 后端 CI 跑了对应测试；固定输入输出还可看 `examples/telemetry-input.json`、`examples/telemetry-output.json` 和 `examples/telemetry-contract-proposal.json`。

| 场景 | 对应测试或断言 | 已验证的边界 |
| --- | --- | --- |
| 正常观测与原值 | `test_normal_preserves_raw`、`test_fixed_json_input_output`、`test_raw_snapshot_is_independent_of_later_mutations` | raw 保留，正常质量为 `ok`，有规则时才可为 `normal` |
| 真实高温与阈值边界 | `test_real_heat_is_kept_and_flagged`、`test_high_temperature_with_missing_humidity_still_candidate`、`test_threshold_equal_is_normal_and_above_is_candidate` | 原始高温不删除；8 °C 等于样例阈值不超，8.01 °C 为候选；湿度缺失同时标记 |
| 缺字段、缺温度、无规则 | `test_missing_field_and_metric_are_unknown`、`test_missing_temperature_never_becomes_normal`、`test_no_rule_is_unknown` | 缺测或无阈值不判正常 |
| 重发与冲突 | `test_duplicate_ignores_new_receipt_time_but_preserves_it`、`test_same_message_id_with_changed_measurement_is_conflict`、`test_same_message_with_changed_warehouse_or_sample_time_conflicts`、`test_message_identity_is_scoped_to_device` | 批内设备＋消息键去重；接收时刻变化可重发，内容变化为冲突；两份 raw 保留，重复/冲突不再给正常结论 |
| 错误 UTC、ID、数值和单位 | `test_invalid_time_is_unknown`、`test_non_utc_time_and_malformed_metrics_keep_raw_unknown`、`test_invalid_unit_does_not_convert_temperature`、`test_invalid_ids_and_values_are_marked` | 非法采样时刻为未知；°F 不静默换算；非法 UUID、非数值温度和超界湿度有质量标记 |
| 非法结构和失败参数 | `test_duplicate_metric_and_malformed_entry_are_marked`、`test_non_object_and_invalid_threshold_fail_clearly` | 非对象观测保留并标记；非有限阈值抛 `ValueError` |
| 契约与查询／CSV 预验收向量 | `test_proposal_observation_sequence_and_filter_example`、`test_query_csv_acceptance_vector_matches_quality_fixture` | 七条固定观测的 raw/质量/重复/候选状态与样例一致；预期分页和 CSV 采用相同筛选及 ID 数量。**未实际执行数据库查询或生成 CSV** |

## 本轮 C0/C1 独立预览增量（2026-10-11）

代码版本 [`f91d8d95d7b7237ca7fb37ae269f9926180445fc`](https://github.com/Cynthia060913/freshlink-zhikong/commit/f91d8d95d7b7237ca7fb37ae269f9926180445fc)，仍基于未含 A01 的 main `9db01387b1c4db2d0ff3f072a199134d7a64643f`。此增量包括 RC1 四条 `schema_version="1.0"` 提案样例、metrics 唯一 key 顺序无关及重复 key 歧义处理、`received_from/received_to` 纯筛选和采样时刻 NULLS LAST 纯排序。函数仍只处理内存中的固定样例，样例 `observation_id` 不来自数据库。

- [Test Backend](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38131628979)：GitHub Actions Ubuntu / CPython 3.14.8，`coverage run -m pytest tests/` 为 **92 passed、55 warnings，项目覆盖率 93%**。新增测试验证高温加重复未知指标不显示为正常或可靠候选、非法采样时间可按合法接收窗及质量找回、采样与接收时间窗同时给出取交集、右边界排除、无有效采样时刻排序末尾及同刻按样例 ID 稳定排序。有效高温 raw 仍保留。
- [pre-commit](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38131628984)、[Zizmor](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38131628983)、[Test Docker Compose](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38131628978) 成功。Compose 是现有通用工程检查，**没有 C 数据表或入库服务可供验证**。
- [Playwright Tests](https://github.com/Cynthia060913/freshlink-zhikong/actions/runs/38131628974) 在 `dorny/paths-filter` 的 `changes` 作业失败，浏览器用例跳过；本次日志原文为 `Resource not accessible by integration`。本增量没有 C 页面，不能宣称页面通过。
- 本机 Git、Python、Docker、origin、未提交改动及 Windows 复现仍未检查，原因是 Codex 本地命令执行器报 `helper_unknown_error: setup refresh had errors`。GitHub Actions 结果仅属于上述云端提交。

## 后续复现与补验收

在具备仓库、Python 3.14 与 uv 的受控环境中，可从仓库 `backend` 目录运行 `uv run pytest tests/modules/test_telemetry_quality.py`，再运行 `uv run pytest tests/modules/test_telemetry_query_preview.py`，核对 `docs/modules/C/examples/` 中的固定 JSON 样例及 `02-query-csv-acceptance.md`。这是复现命令，**未在 C 本机运行**；安装与执行是否成功应由执行者记录机器、提交 SHA、命令及输出，不沿用 CI 结果冒称本机通过。整仓 CI 还依赖数据库与现有脚本环境。

正式接线后需单独留下可审计证据：在已合并 A01→B 的当前迁移链上验证原始观测和质量的入库、跨重启同键重发/冲突/事务失败；以固定观测对照实时与历史筛选、稳定翻页、非法时间质量检索、相同权限与筛选下 CSV 的实际行数、转义和公式注入防护；验证 B 规则快照版本、候选幂等投递、超时和永久拒绝；再将 MQTT→入库→B 持久告警→实际通知→控制回执→后续数据变化的 E1 证据记入 [#6](https://github.com/Cynthia060913/freshlink-zhikong/issues/6)。目前上述项目均未执行，不填写品质概率、保质期或节能效果。

共享依赖详见 `01-contract-proposal.md` 和 `integration-notes.md`；测试结果与当前版本以 [#9](https://github.com/Cynthia060913/freshlink-zhikong/issues/9) 的最新阶段记录为准。
