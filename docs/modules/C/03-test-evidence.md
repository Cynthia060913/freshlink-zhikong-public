# C 测试证据与后续复现清单

状态：C4 的阶段性真实证据汇编；更新于 2026-10-15。当前实现含 C0 纯处理/A 接收封装投影、C1 内存筛选/CSV 字节预览，以及 C2 的固定 F03 候选纯投影；**没有**正式存储、数据库查询、权限、HTTP 导出、候选持久交付或 E1 联调。以下“当前新库证据”按明确代码 SHA 解释；更早章节按当时状态保留，其中“本机待上传”等描述已被后续远端提交覆盖，不能当作当前阻塞。

## C2 固定候选纯投影增量（2026-10-15）

- C 自有代码提交 `2aac6e476190c0875d2094b4d1346fa1dd950782` 新增 `candidate_preview.py::build_candidate_preview`。函数消耗已处理高温、外部已分配的 `observation_id`/`source_event_id` 和**合成规则快照**，生成与 `examples/candidate-f03-rc2-c-proposal.json` 一致的单批 JSON；缺规则、缺温、错单位、无规则状态、重发和冲突不发候选。仓库不匹配、规则不在采样时刻生效、单位/阈值不一致或来源 ID 非 UUID 均显式拒绝，原始观测和规则输入不变。它不生成或持久保存来源 ID，也未调用 B 服务。
- Windows/Codex Python 3.13.9 在 `backend` 目录实际运行五个 C-only `unittest` 模块，**74/74 OK**（原 71 项加 3 项构建/不发/错误规则测试）；`git diff --cached --check` 通过。一次尝试把 A→C 导入边界模块加入同一命令，因 A 的 Python 3.14 多异常语法在本机 Python 3.13 解析失败，随后只运行上述五个 C-only 模块；没有修改 A 源码，也不把失败尝试称为通过。项目 Python 3.14、B DTO 实际接收、持久 outbox、跨重启幂等、PostgreSQL、HTTP、页面和 E1 仍未测试。本提交的远端检查须以 PR #11 对应 SHA 的实际运行结果另行记录。

## 当前新库证据与 M03 测试章节底稿（2026-10-15）

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
