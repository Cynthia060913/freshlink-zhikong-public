# C 数据处理与验证章节草稿

## 2026-10-29 新 f6 来源的 C 第二机有限入口实测

共同 main 已合 PR #98，合并提交 `1038983bf3232bc2345a3e150a85c862eec3ec6a`；本次应用精确源码为 `f6f11941e2f3ba286d3286e664e80b3b0145e1da`，与下方 `03b6002/e7` 旧包分段严格分开。C 本人在已有 Docker Desktop Linux x86_64 的 Windows 第二机核得新 ZIP **715821697 字节**、SHA256 `046a76eaba8ed77bcff4b21589f8d399490fcca37f83600542110874bf125190`；manifest SHA256 `b69e74ae26010706895ae38a99566778f75b1729d45981baea64189e11095917`。新目录解压后，先只读校验通过，再显式运行一次五分钟随机项目和空库；工具核了包内镜像 ID/应用来源标签，没有源码挂载或真实通知。

浏览器实际登录、设备页 **14 台在线**；第 1 台制冷执行器命令 `302b6c56-c6cc-4026-8678-507a1f0633ed` 从排队转为服务端保存的设备执行成功回执（发送尝试 1），随后同设备测点有新已存观测。时间顺序不单独证明温度变化均由这条控制引起。浏览器遥测页按该设备建一次固定快照，页面总数 **264**；浏览器按钮实际下载 CSV **65108 字节、14 列、264 行、264 个互异观测 ID**，SHA256 `eaef55365ebb7773cf398003d03ed186f598b30ad30f6ccf3d811b6f65c2ce5b`。本次未完成与全部分页逐 ID/顺序对拍、匿名历史 401、普通用户控制 403，因此不借用 A 或旧 e7 的结果。运行报告四个后台子进程退出码均 0、线程清理正常；限定停机后本轮容器/网络均为 0，专属数据库卷保留。自动恢复和真实通知均未开启；原 e7 一库同事件 E1 与两渠道实收仍见 #6，不重跑也不改标。本次本人步骤和边界见 [#9 新 f6 实测](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6489450099)。

## 2026-10-29 最终离线入口的 C 第二机实测增量

本次发布入口包属于 `03b60028517b4a2d63dcfb41ab2c620b832967b7`，
其中应用镜像来源为 `e7fffd61c199234918dcf1713cc1f3dd6ce78c51`。
收到的 `FreshLink-03b6002-published-interactive.zip` 实测 719157924 字节、
SHA256 `632ee61c5bfb070ca5f423a06778259e256c773b3318006bc41dcee8095fdd57`；
内层 manifest SHA256 为
`609b072f373b1db3abb717427dbb05c5987c45a0b0caef941f160d0751ba7c7c`。
C 本人在另一台 Windows 电脑以已有 Docker Desktop Linux amd64、PowerShell 7.6.5
完成完整性校验和五分钟随机隔离项目实跑。浏览器实际登录后见 14 台仿真设备在线；
第 1 台制冷执行器一次命令取得设备执行成功回执，随后传感器有新增已存观测。
这证明了同版入口的控制和后续采集，不能仅凭时间顺序断言该命令造成了全部温度变化。

本人仅新建一次同设备固定快照，页面显示 257 条；遍历 13 页共 257 行，
并从浏览器下载真实 CSV 文件：14 列、257 行、257 个不同观测 ID，
文件 SHA256 为 `1f27794f72609f8008a5629c6daf3255c002a24e1d48fa7c86d6bf5c674e95d7`。
CSV 与页面全部分页的设备 ID、采样/接收 UTC、规范化温度逐行同序一致。
匿名历史接口返回 401；本轮包只有临时管理员账号，普通用户写入 403 未测。
五分钟四个工作子进程退出码均为 0，线程清理正常；本轮容器和网络已停止，
专属数据库卷及证据保留。真实通知和自动恢复在此包默认关闭，未发送通知。
自然 180 秒持续恢复没有在这个交互包重测。完整输入、操作、退出和未测见
[#9 本人第二机记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6475341383)。

记录时的新共同 main 为 `9978f0f0e7f36372f2e151e9859ca01ed7cdb37a`，
仅比本包增加 PR #97 的单邮箱正式发送护栏；**不把旧包标成新 main 的镜像**。
一库同事件 E1 仍以 [#6 A 的独立签收](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6459911129)
为准；此处为 C 的另一次第二机入口复现，不是重发两渠道或完成最终投稿。
下文 2026-10-28 的“浏览器 CSV 未核验”只描述那次旧分段，当时的结论保留。

## 2026-10-28 同版签收后供 M03 汇编的当前摘要

共同 main `319aa6363d921922070b4330bf74e344a8b0c1bb` 已含 A 审核整合的
C 旧高温原文重传修复；PR #94 精确源码
`e7fffd61c199234918dcf1713cc1f3dd6ce78c51` 与已发布 main 全 Git 树
一致。C 的原始观测、质量、幂等、历史/固定快照 CSV、候选及恢复实现按此版
引用，不再把下节 main0e/PR #11“尚未合并”当作当前状态。

[A 集成方一库同版同事件 E1 签收](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6459911129)
记录认证 MQTT→C raw/质量保存→B TCP 异常、同源 SMTP 与飞书各一次且队长
本人确认两端收到、控制执行 ACK、10.027607→7.966898°C 持续自然恢复、
真实浏览器 281 行/14 列 CSV 与 15 页观测 ID 一致、普通用户写操作 403。
这属于 A 的独立同库实链，不写成 C 本机重跑。C 在 Windows 第二机独立构建
同精确源码，另验证无挂载默认启动/真实 HTTP、两库三批 14 设备和普通用户
设备/历史/告警页面；同设备固定快照 API CSV 文件 10 行/14 列、ACK 403、
匿名 CSV 401。浏览器按钮已点但下载文件路径未取得，本人该项仍未核验。
详见 [C 团队测试报告](07-team-test-report-draft.md) 与
[#6 本人分段记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6449022153)。
全部设备与观测为**软件仿真**；没有真实苹果品质概率、货架期、节能效果或
可选模型实测。本章下方各节依旧是对应日期的历史证据，不得覆盖当前摘要。

## 2026-10-28 旧版阶段摘要（main0e，当时可供 M03 汇编）

共同运行源码 main 为 `0e508d147517f81dfe2b9245c45c0c0e30076592`；C 恢复修复
`18a0f4ad4920e511d96388cd826527c8c4d777aa` 已推到 Draft
[PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，尚未合并。
因此数据接收、查询页面和旧高温重传续验分别按其实际 SHA 引用。

- 本人第二机在 main0e 的随机空库已过默认应用镜像真实 HTTP、两库三批 14 设备
  存储与控制分段。一次性普通用户实际浏览器登录后，遥测页按设备筛选出固定快照
  19 条，并可读保存的原始 JSON 和重复接收审计；过旧观测显示风险未知。
  CSV 下载按钮已触发，但没有得到可检查文件，不能把本轮页面操作说成 14 列和
  19 行导出一致性已签。详细对象、输入和证据见[测试报告](07-team-test-report-draft.md)。
- C 修复只让冻结前已有同设备/冷库/epoch 的首次可信入账见证、原文及摘要均一致的
  高温重复接收通过恢复前沿续验；它不删除旧高温，也不把新增高温或伪重复当恢复。
  本人隔离 PG18 相关测试 5/5 通过；A 的 10°C→8°C 持续采集自然恢复和完整 E1
  仍待同版实跑。不能用项目测试或第二机分段替代。
- 本章只叙述仿真设备与真实软件处理链的证据，不推断真实苹果品质概率、货架期
  或节能效果。下文较早“未入库/未挂载”等段落保留为对应历史日期，非现行状态。

## 2026-10-26 合并版交接摘要（供 M03 引用，非最终验收）

共同 main `0e508d147517f81dfe2b9245c45c0c0e30076592` 已包含
A [PR #88](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/88)
的恢复发送接线，以及先前进入共同源码的 C 原始观测、质量、历史/快照/CSV、
候选、恢复与更正模块。当前 `backend/app/api/main.py` 实际挂载 C 的
`telemetry_router` 和 `telemetry_audit_router`；源码存在与路由挂载是代码核对，
不自动证明冻结版在另一台电脑启动和完整 E1 通过。旧章节按各自日期保留，
其中“未实现”“未挂载”和旧私有仓库链接不能当作合并版实时状态。

| 本轮可引用事实 | 实证边界 |
| --- | --- |
| 设计：保留 exact raw/reception，规范化值和质量标记分存；真实高温保留，缺测/坏单位/无规则保持未知；同设备消息及来源事件按冻结正文处理重发与冲突 | 固定 JSON 为人工构造，不能推断真实冷库品质、货架期或模型效果 |
| 数据与查询：源码有持久候选、历史/详情、固定成员快照和 14 列 CSV，C 路由已挂在共同 API | C 本人同 #88 源码在隔离 Python3.14.3 上运行六组数据/历史/鉴权 CSV/多批次测试 **69/69**；这是模块测试，不是最终跨机页面/数据库/CSV 业务验收 |
| 恢复：A #88 整合 C 同 Connection 领取、提交后锁外发送和 A 默认关闭的独立发送器 | C 本人相关 unittest **22 passed/7 PostgreSQL 条件 skipped**；A 自有 PG/MQTT/TCP 证据按 A65 引用，不能写成 C 在本机复跑；双渠道终端实收和完整 E1 仍待 |
| 第二机：C 独立 Windows 电脑只读确认 Docker 29.9.2、Compose 5.6.1、Linux engine 可用 | 旧 `96afaab8…` 离线包不能冒充当前冻结版；待 A 指定同版源码/镜像/manifest 与 SHA256 后实际复现，见 [C 测试报告](07-team-test-report-draft.md) |

本章最终签收需同一发布 SHA 的实际输入、输出、权限、重启和失败记录；
M03 汇编只引用相应证据，缺少的真实通知、完整 E1 和第二机结果保留“未测”。

## 当前可引用范围（2026-10-20；候选头 `7565fbd2863e6ff4d9bfe1ac1d798506f89c8390`）

本章供 M03 数据与测试部分汇编，**不等于最终发布版的实测结论**。现行开发入口是
[公开仓库 C PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)；
旧私有仓库链接和下方按旧日期写的“未实现”只对应当时版本，不是当前代码状态。
最终稿必须按正式合并 SHA、同版运行与团队证据重新核对。

| 范围 | 此头已有的候选能力 | 尚不能写成已验收 |
| --- | --- | --- |
| 观测与质量 | 保留 exact raw text、可信接收封装、规范化值、质量与每次 reception；隔离事务表支持同消息/接收 ID 幂等和冲突审计；高温不删除，坏单位/缺测为 unknown | 正式唯一 Alembic 迁移、主程序 MQTT→C 连续真实入库及生产 PG 重启 |
| 查询与导出 | C 相对鉴权 HTTP 候选、历史/详情、同 actor/筛选的固定成员快照分页和 14 列 CSV；手动新建快照；坏单位原文可在详情读回，CSV 人工查看防公式前缀 | 公共根路由/导航、持续实时刷新和坏接收的页面状态、同版正式 PG 页面与 CSV |
| 异常与恢复 | 消费 B 历史规则的温度候选与持久 outbox；已存坏 reception、冲突和全局接收 ID 跨设备异文复用会保守 hold 恢复 | A/C 提交前沿、跨事务并发、后台调度、多批次独立评价、B 实际接收/恢复及 E1 |

固定的七条遥测与筛选样例为**人工构造**，只用于接口和失败边界复现；不计作真实冷库采样、品质标签或算法效果。精确 C 头本人 Windows 隔离 Python3.13/SQLite 恢复相关
**39/39**、Ruff lint/format 与 Git 差异检查通过；候选 HTTP 测试因本机没有
Python3.14 未在本机运行。远端 Python3.14 [Backend run39428737160](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39428737160)
为 **550 passed/4 skipped/108 warnings**，同头五项适用 Actions 成功；4 skipped
不得算正式 C PostgreSQL 验证。原始证据及分段限制见
[C 测试证据](03-test-evidence.md)和[#9 状态](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6263404899)。

状态：2026-10-12 C4 模块素材，供团队 M03 软件设计文档选用；**不是**完整系统验收报告、真实采集统计或 M06 模型性能报告。正式发布时以同一实际发布 SHA 复核代码、截图、表格和本文。对应主任务 [#9](https://github.com/Cynthia060913/freshlink-zhikong/issues/9)、草稿 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong/pull/11)。

2026-10-16 当前补注：新库 [C PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 已推进至 `bdab8a81a0b92bae4a3292585e329e4de66da598`。除下文历史纯预览外，C 自有**隔离候选表**已有事务写入、SQL 历史读回、固定成员快照和同快照 CSV 字节，真实接收 ID 复用的不同原文也另存拒绝证据；详见 [最新测试证据](03-test-evidence.md#当前可交接证据索引2026-10-16)。这仍不是正式 PostgreSQL 迁移、应用入口/权限、正式查询下载或 E1。下文早期各节的“没有跨重启事务幂等/数据库查询”等是当时纯函数版本，不能当作当前候选代码状态；最终 M03 须在合并后的真实发布 SHA 上重写实测段落。

2026-10-15 新库续作注记：以上旧库链接只作历史证据；当前开发入口为 `Cynthia060913/freshlink-zhikong-public`。受检 C 代码提交 `e0f14c44c19597c925ca5e3ed387d8f01d1796cd` 已普通推送至[新 PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，Windows/Codex Python 3.13.9 五组 C-only 测试 **76/76 OK**；该 SHA 的后端、Compose、pre-commit、Zizmor、Playwright 五个工作流均成功。接线说明提交 `c08829be8cc4437a34e9f442bf606448b9cfa7ce` 另刷新[接线边界](integration-notes.md)，是纯文档提交，其同 SHA 五个工作流也成功；没有新增正式 PostgreSQL、HTTP 或 E1 测试。完整运行条件与缺口见 `03-test-evidence.md` 顶部及[新 #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6181488019)。下文较早测试数、旧私有链接和“本机未检查”叙述保留为当时记录，不代表当前状态。

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
| 实时/历史页面及权限 CSV | 未实现 | 同 actor、同筛选、同服务端固定结果快照核对页/导出 ID、行数、单位、空值、失败码和大于 10000 行拒绝 |
| B 历史规则和异常/恢复候选 | 未实现 | 真实版本、采样时刻、批次、来源 ID、持久 outbox/超时重试和 B 回执；缺规则/缺测保持未知 |
| MQTT 与 E1 | 未执行 | 一库一测点一执行器的真实消息、入库、告警、通知、控制 ACK 和后续数据证据，按 #6 留记录 |
| 累计暴露或可选模型 | 未开始 | 必选闭环后才定公式/单位/训练划分/独立实验/系统调用；无真实结果不填品质概率、货架期或节能率 |
| 跨机与团队测试报告 | 未完成 | A/B 各自交本人测试证据，C 核版本和限制后汇编；另一电脑同版实际复现 |

本章只描述 C 当前真实贡献和缺口。最终 M03 的架构、ER 与完整业务结果由团队合并后的同一版本核对；若采用算法，M06—M08 需另附真实代码调用路径、生成规则、样本量、原始输出和局限，不从此固定阈值样例推断模型效果。
