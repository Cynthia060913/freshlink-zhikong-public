# 鲜链智控团队测试报告底稿（C 汇编）

## 2026-10-31 当前发布点的 C 第二机有限验收

本节是新的独立运行，更新下方带日期的旧状态，不改写旧试验。共同发布点
`1a2fa53be38a83a42df65395adf88332d5cdf3ed` 与受测应用/工具源码
`4b9bd50484503d8b02d5f87405490c7b13aae5e9` 全树一致。
指定 M02 ZIP 实测 715242775 字节，SHA256
`1af60e09604dc0a871718f6dd198db5bef050fb46fc0d4eaee248255b6aada90`；
内层 manifest SHA256
`93ebde00c5a32f5b14d2442e89e6f059d962fd0953dd29bb9845536177956f1b`，
完整性只读校验通过。本人 Windows Docker Desktop 上另建随机专属项目和空库，
没有源码挂载、真实通知或自动恢复。

| 范围 | 本人实际结果 | 限制 |
| --- | --- | --- |
| 浏览器设备与控制 | 本机 Chrome/Playwright 管理员登录，页面显示 14/14 在线；对一台仿真执行器仅下发一次关闭制冷，页面取得设备执行成功回执，随后同设备测点有新的已存观测 ID。 | 时间先后不能单独证明全部温度变化由控制造成。 |
| 浏览器历史与导出 | 同一固定快照遍历 7 页、123 个互异观测 ID；页面按钮实际下载 75959 字节的 15 列 CSV，123 数据行，SHA256 `106633e000c8cced746d3d7bb55541570be4cee0f0cb340f3003d256755c1007`；全部 ID、行序与 15 字段逐行一致，含逐批引用 JSON。 | 这是真实浏览器下载，不是 API 保存文件。 |
| 逐批表示与兼容 | 浏览器展开多批次规则，显示批次 ID、版本、快照 ID 和状态。**另一份**固定快照的默认 API 14 列和显式 v2 15 列各 269 数据行，前 14 列逐格一致。 | 269 行与上面的 123 行不属于同一快照，不相加。 |
| 权限 | 匿名历史 GET 401、匿名 CSV GET 401；一次性普通登录账号控制 POST 403。 | 是实际请求结果，不以按钮隐藏替代。 |
| 运行与清理 | 五分钟报告 `bounded_demo_completed`；14 逻辑设备、四后台角色退出码均 0、worker_errors 为空；受限停机后本轮容器 0、网络 0、专属数据库证据卷 1 保留。 | 未启用自然持续恢复，也没有重跑原 E1 或发送真实通知。 |

本人完整操作、结果文件哈希及首次测试脚本假设修正见
[C #9 6527639497](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6527639497)；
第二机联调增量见
[#6 6527719236](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6527719236)。
原 `e7fffd61` 一库同事件 E1 和两渠道同来源终端实收保留各自来源，不能改标为
本次 4b9 运行；本节不代表 M01—M05 总包签收或实际投稿。下文旧日期段落的
“未测/未完成”只描述当时版本和试验，不能替代本节当前范围。

## 2026-10-30 基础版候选与 f6 第二机补验（C 本人）

| 来源 | 实际验证 | 未证明的范围 |
| --- | --- | --- |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 业务源码 `ab2a7b6c4d71a11e6db665c94ee5f10a46275c26` | 本机从该提交建立隔离检出，将 backend 只读挂载到旧 f6 镜像内的 Python 3.14.8/锁定依赖。无网络、临时 SQLite/TestClient 的历史/快照/HTTP 三组 unittest **21/21 OK**。另用随机私有网络和 tmpfs PostgreSQL 运行 C opt-in 专项 **15/15 OK**；本轮 PG 容器/网络清理后均为 0，无测试数据卷。该业务源码的远端六项检查成功，Backend **1490 passed / 2 skipped**；后续纯文档提交不改变测试来源。 | 新源码运行在旧 f6 测试环境，**不是新应用镜像**；尚未在新镜像/第二机浏览器验证逐批规则页面和显式 v2 CSV。21、15 与 CI 数字分属不同运行范围，不相加为总通过数。 |
| 已发布 main `1038983bf3232bc2345a3e150a85c862eec3ec6a` 的 f6 应用 `f6f11941e2f3ba286d3286e664e80b3b0145e1da` | 在本人 Windows Docker Desktop 第二机上，沿用已核离线包、重新建一次随机项目/空库，内部 manifest 校验通过。匿名历史 GET **401**；新注册普通账号的有效登录凭据 POST 控制 **403**。同设备一份固定快照的全部 **14 页/265 个互异观测 ID** 与同筛选 HTTP CSV **265 行/14 列**逐 ID、顺序一致。五分钟受控采集正常结束、四个后台子进程退出码均 0；本轮容器和网络清理后均为 0，专属数据库卷保留。 | 此次对拍的是 **HTTP CSV 响应，不是浏览器下载文件**；下节 264 行浏览器下载属于前一次不同快照。旧 f6 没有本 PR 的 v2 导出修复，不能把此补验记到 `ab2a7b6…`。通知与自动恢复关闭，没有重跑旧 E1 或再次实发。 |

两组本人的完整命令边界和结果分别见 [C 精确头复验](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6503083617)与 [f6 第二机补验](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6502694128)。下节 10 月 29 日的“本次未测”专指**当次** 264 行快照，不覆盖今天独立项目的补验；保留其当时记录，不追改旧试验结果。

## 2026-10-29 新 f6 来源第二机有限入口（C 本人）

| 项目 | 本人实际结果与边界 |
| --- | --- |
| 精确版本/制品 | main 合并 `1038983bf3232bc2345a3e150a85c862eec3ec6a`；应用 `f6f11941e2f3ba286d3286e664e80b3b0145e1da`；ZIP 715821697 字节，SHA256 `046a76eaba8ed77bcff4b21589f8d399490fcca37f83600542110874bf125190`；manifest SHA256 `b69e74ae26010706895ae38a99566778f75b1729d45981baea64189e11095917`。新仓库外目录，verify-only PASS 后一次显式五分钟运行；Docker Desktop `linux/x86_64`，新随机项目/空库，镜像 ID/来源标签门禁通过。 |
| 入口和控制 | 健康 200、本机随机管理员登录；API 设备数 14、浏览器设备页 14 台在线。浏览器一次开启制冷命令 `302b6c56-c6cc-4026-8678-507a1f0633ed` 显示排队→设备执行成功回执，发送尝试 1，随后测点有新已存观测；不从时间顺序单独推断全部温度变化的因果。 |
| 页面/CSV | 浏览器遥测页对设备 `10000000-0000-4000-8000-000000000001` 建一次固定快照，总数 264。浏览器按钮实际下载 `observations (1).csv`，65108 字节、14 列、264 行、264 个互异 observation_id，SHA256 `eaef55365ebb7773cf398003d03ed186f598b30ad30f6ccf3d811b6f65c2ce5b`。**未逐页保存全部 14 页 ID/顺序对拍**，不能写成该项已过。 |
| 退出/清理 | `bounded_demo_completed`；初始观测 74、逻辑设备 14；receive/store/evaluate/deliver-high 四子进程退出码均 0，线程清理 true、worker_errors 空。限定 stop 后本轮容器 0、网络 0、专属 DB 卷 1 保留；原库未动。真实通知与自动恢复均关闭。 |
| 本次未测 | 匿名历史 401、普通用户控制 403、CSV 全部分页逐 ID/顺序对拍、180 秒自然持续恢复、真实邮件/飞书通知；A 的 f6 本机证据和 C 旧 e7 证据不并入此行。 |

公开的本人操作与边界见 [C #9 6489450099](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6489450099)。原 e7 一库同事件 E1 及两渠道实收是另一版本/试验库的既有签收，不重标为本次 f6 第二机完成项。下文 `03b6002/e7` 旧包保留独立历史。

**f6 数据字段复核增量：**浏览器 CSV 的 6 条整体 normal 与 258 条 candidate 均未在单列显示规则版本。本人在本轮隔离 PG 保留卷执行只读汇总：同设备 270 条已评估多批次记录各有 2 个批次，540 个批次结果均含非空规则版本和规则快照 ID；6 条整体 normal 对应 12 个正常批次。另有 1 条初始 unknown/no_threshold_rule 待评估记录。精确 f6 源码核对显示，版本在 `risk.batches[*]`，F06 投影只读顶层版本；因此空列是多批次展示/追溯缺口，不是这 6 条“缺规则判正常”的证据。不能把页面“未匹配”解释为无规则，也不能宣称本版 CSV 展示逐批版本。只读核验后临时容器/网络清理为 0，专属证据卷保留；详见 [#5 核查](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6489558890)。本轮未改生产源码或重建 f6 包。

## 2026-10-29 发布离线入口：C 第二机浏览器与停机验收

| 项目 | 本人实际结果与边界 |
| --- | --- |
| 来源/环境 | 外层交付提交 `03b60028517b4a2d63dcfb41ab2c620b832967b7`；应用镜像 `e7fffd61c199234918dcf1713cc1f3dd6ce78c51`；C 的 Windows 第二机、已有 Docker Desktop Linux amd64、PowerShell 7.6.5。新空数据库、随机项目，无源码挂载、无真实通知。不是后来的 main `9978f0f0e7f36372f2e151e9859ca01ed7cdb37a` 再出包。 |
| 制品完整性 | ZIP 719157924 字节、SHA256 `632ee61c5bfb070ca5f423a06778259e256c773b3318006bc41dcee8095fdd57`；包内 manifest SHA256 `609b072f373b1db3abb717427dbb05c5987c45a0b0caef941f160d0751ba7c7c`；只校验脚本 PASS 后才显式启动五分钟窗口。 |
| 浏览器业务 | 管理员实际登录；设备页 14 台仿真设备在线。一次开启制冷命令 `6047b9e1-fb76-4753-ba3b-357eab13b78a` 从排队变为 MQTT 接受、设备 executed ACK，发送尝试 1；随后同设备传感器新增采样 `2026-10-28T16:49:54.947922Z`、接收 `16:49:55.055275Z`、6.104996 °C 的已存观测。时间顺序不单独证明控制因果。 |
| 页面/CSV 对拍 | 按一设备建立一次固定快照，页面总数 257；本人遍历 13 页共 257 行。浏览器按钮实际下载的 `observations.csv` 为 63387 字节、14 列、257 行、257 个互异观测 ID、仅该筛选设备；SHA256 `1f27794f72609f8008a5629c6daf3255c002a24e1d48fa7c86d6bf5c674e95d7`。逐行核对全部页面：设备 ID、采样/接收 UTC 和规范化温度顺序相同。此项确为浏览器下载文件，区别于 10 月 28 日旧分段的 API 保存文件。 |
| 权限/退出/清理 | 匿名 GET 历史接口实际 401。本轮仅有临时管理员账号，普通用户写入 403 **未测**；旧分段普通用户 403 证据不冒充此次重跑。`interactive-runtime.json` 为 `bounded_demo_completed`，receive/store/evaluate/deliver-high 四子进程退出码均 0，线程清理正常、worker_errors 空；包内专用停机后本轮容器 0、网络 0，专属数据库卷与结果保留。没有 prune、删旧库或发通知。 |

证据与操作边界见 [C #9 完整实测](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6475341383)。
此行核销外层离线交互入口的第二机范围；自然 180 秒持续恢复、本包普通用户
写入 403、最新 main 的新镜像、真实通知新发送和最终投稿均未由此证明。
[A 一库同事件 E1 签收](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6459911129)
使用另一独立试验库，双渠道终端实收由队长本人确认，不能和本次入口运行拼成一次试验。

## 2026-10-28 C 第二机 PR #94 精确候选与普通用户验收

共同 [PR #94](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/94)
精确源码 `e7fffd61c199234918dcf1713cc1f3dd6ce78c51` 已在本 Windows
第二机独立构建。源码 ZIP SHA256
`b90a7b33e827fe8720092ae51db7e15e1c08cb298b05e5b62b44e857bb1b93e2`；
本人镜像 ID 为
`sha256:9be10a60c147722a68e4813aeebaefa67c0d0cad1815623e213973aafd70ef27`，
与 A 独立构建的镜像 ID 不同。无源码挂载、默认 CMD、随机空 PG18 的
真实 HTTP 冒烟通过：健康、匿名 401、管理员登录、注册 201/重复 409 与
详情读取。报告 SHA256
`D987E196E88FDF8EEE3C5CCA2D357BF6C05ABA8FEE9389EBEFE3C7A009E63DCA`。

同镜像另一个随机项目运行 14 设备夹具：两冷库、三批、七条实际 C
采集流、七个执行 ACK 与后续采样、14 个离线/恢复追溯；两库快照 CSV
脚本回报 40/27 行，B ASGI 接受三批高温事件。夹具 JSON SHA256
`2F592CDCBCFDB748E2BC3CBCA4370D2A6F397614D213CE172284AC208FEB8D6A`。
该夹具未运行 180 秒持续段，`notifications_sent=0`、`complete_e1=false`。

在该项目实际网页以一次性普通用户登录：设备页有 14 台及两库关联，
设备详情有心跳和离线/恢复来源；遥测页按一设备得到固定快照 10 条，
可看原始 JSON 及重复接收；告警页列三条且普通用户只读。页面 CSV
按钮已点击，但未取得浏览器管理的下载文件路径，**不能声称浏览器下载
文件已经核验**。另以同普通用户 HTTP 获取同设备固定快照及 CSV 文件，
结果 200、10 行、14 列，全部属于筛选设备；该 API 文件 SHA256
`23CA765A87769081F63DE19DAD512BC25A26FFDD9DBF2CE28C555D1939994614`。
随后只读复核保留的同一 CSV 原文件：10 个互异的 `observation_id`，仅
一个设备，行数、列数与 SHA256 均与首次记录一致。原快照响应仅保留
`total=10`，没有保存完整 ID 列表；一次性 PG 已清理，故本机**未证明
快照与 CSV 的全部 ID 集合相等**，也未核验浏览器管理的下载文件。
普通用户 POST 告警确认返回 403，匿名 CSV 返回 401。页面操作与 API
文件分别记账。证据见 [E1 #6 本人记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6449022153)与[保存制品补核](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6461583849)。

本次专属 UI/PG/MQTT 容器及网络清理后均余 0，无真实通知。PR #94
随后合入 main `319aa6363d921922070b4330bf74e344a8b0c1bb`，但本试验
运行于 e7 精确检出，不能称为合并后重新运行。未测本次第二机自然持续恢复、
浏览器实际下载文件、同一事件两渠道终端实收及本次第二机完整 E1。A 的原 10°C
自然恢复和 291 行同快照 CSV 见 [其独立记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6448255164)，
不能与本机分段拼为一次完整 E1。

随后 A 在[同版同事件的一库 E1 签收](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6459911129)
记录原来源 `d89c0bf9-f063-537b-a5e0-08866e4a0b74` 的实际 MQTT/PG/TCP B、
授权后 SMTP 与飞书各一次且队长本人确认两端收到、执行 ACK、持续自然恢复、
真实浏览器下载 281 行/14 列 CSV、15 页观测 ID 一致与普通用户写操作 403；
验收 JSON SHA256
`13500940875c0fd8aed244ad9d2a75907a75910a2d3cd39a5474428a36856470`。
这是 **A 集成方签收的一库 E1**，与本机第二机分段分别记账，不写成
C 本人重跑或最终第二机总签收；M01—M05 与实际投稿仍待完成。

## 2026-10-28 C 第二机与新共同基线（待同版自然恢复）

共同 main `81eb3d3a1ef4c62f3dd449d7df14a4ef41fa513e` 已合入 A 的
PR #93。C 在干净本人分支同步此 main，组合源码精确提交
`a3737f6fcebb39888884f541f178257254600534`，相对 main 只剩两份 C
源码/测试和两份 C 文档。Windows 第二机使用该检出，通过只读挂载业务源码、
旧 main0e 标签的已核验应用镜像仅提供 Python 3.14 依赖；业务身份以本次
`source` 字段为准，不能把依赖镜像标签误记为本次业务版本。

在随机独立 PostgreSQL 18 tmpfs 与内部 MQTT 网络，C 恢复严格 PG 专项
`test_telemetry_gated_high_postgres_candidate.py` **5 passed / 0 skipped / 2 warnings**。
随后运行共同 `check-gated-fleet.ps1 -Sustained`，181.94 秒分段退出 0、
`passed=true`：两库三批、14 逻辑设备、七条实际采集流，36 个采样快照的
最大待入账数 8、最大流滞后 2，全部 gap 原因为空；两库同筛选快照 CSV
脚本分别回报 654/488 行，三批高温经 B ASGI HTTP 分段记录。结果
`fleet-sustained.json` SHA256
`550D64537F228FFADA9DFCF28D37EF08D33EBC95B4AB009AC19F418A832691B4`，
`gated-fleet.json` SHA256
`07419C22D4F2DF2D0F62B25BEE617B04D0FE12C6657499BC1FE3C117B3FFF331`。
本轮 `notifications_sent=0`、`complete_e1=false`，没有运行 10°C→8°C
采集不停自然恢复，也未取得浏览器下载 CSV 文件；专属项目容器/网络回读均 0。
此前旧 main0e 的 `wait_for(drained)` 失败保留在下表，不作为新组合失败。

## 2026-10-28 C 第二机交互与旧高温重传修复（待同版自然恢复）

本节记录以下带日期的旧状态，当时共同运行基线为
`0e508d147517f81dfe2b9245c45c0c0e30076592`；C 新修复已在本人分支
`18a0f4ad4920e511d96388cd826527c8c4d777aa` 上传到
[PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，**未合 main**。
因此本节的第二机页面结果属 main0e，C 修复专项属新 C 头，两者不可合称同一发布版。

| 对象与输入 | 本人实际结果、证据与限制 |
| --- | --- |
| PR #92 只增加白名单诊断后的受控恢复，main0e 源码与镜像 | 专属空库 `gated-937aa510315241f892909558b4941ff5` 退出 1，仍停在 `stored_cooling` 的 25 秒检查；ACK 后最后 C 已存值 10.013366°C，设备模拟值约 9.681747°C。诊断 JSON SHA256 `F32A51B091D422D6EB637563FE4033A88F10A9996523227253AECDC8918DF746`；清理后专属容器/网络均 0。诊断只定位检查点，未证明根因，不能算受控恢复通过。见 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6446634862)。 |
| main0e、固定镜像、另一随机专属空库、一次性普通用户浏览器 | 本人登录并看到 14 个仿真设备；执行器详情显示已存执行成功回执且普通用户不可下新命令。遥测历史默认快照 132 条，按一设备建快照 19 条；详情可看原始 JSON、质量及首次/两次同原文重复接收。最新数据过旧提示风险未知。告警列表 3 条，详情普通用户只读。CSV 按钮已点，但浏览器没有返回可核对文件，**14 列/19 行未实测**；API 403 未另测。测试 UI、临时库和网络已按专属标签清理为 0；不代表完整 E1。见 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6447295714)。 |
| main0e 同镜像另一次 CSV/权限补核尝试 | 新随机专属空库 `owned-review-ff87baf169dd4162b93ef29d4a077469` 的 14 设备夹具在 `wait_for(drained)` 超时退出 1，尚未打开页面或执行 CSV/API 检查。保留前一轮已过交互分段，不把它与本次失败拼为一次通过；未反复重跑旧 main。失败项目容器/网络回读均 0，未外发通知。见 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6447709022)。 |
| C 旧高温原文重复续验，精确修复 `18a0f4ad4920e511d96388cd826527c8c4d777aa` | 本人 Windows 第二机以项目 Python 3.14 镜像、随机 PG18/tmpfs、唯一迁移至 C01，相关严格 PG 文件 **5 passed、0 skipped、2 warnings**；覆盖旧高温确证重复可续验、坏摘要/历史原文/伪重复拒绝，且新高温、gap/epoch、领取和冻结正文不变原断言保留。Ruff 与暂存安全检查通过，清理后容器/网络 0；同 SHA 六项适用远端 Actions 完成成功。这是专项候选，A 尚须在同版组合重跑 10°C→8°C 持续采集自然恢复及失败路径。见 [C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6447282052)。 |

其他成员证据仍按其本人 SHA 和环境引用，不与以上结果求和。双渠道同来源
实收的最终核销、完整 E1、冻结版第二机全部项目与参赛投递均需另行签收。

## 2026-10-27 C 第二机同版续验：14 设备分段通过，受控恢复未通过

在独立干净检出 `0e508d147517f81dfe2b9245c45c0c0e30076592` 上，先对项目固定的
`eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408`
做官方仓库只读 manifest 核验，再按**该精确摘要**显式拉取一次。下载后 `image inspect`
回报相同 RepoDigest、image ID `sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408`
及 `linux/amd64`；应用镜像 ID、来源 label 与离线包清单仍吻合。没有改脚本的摘要门禁，
也没有让验证脚本自动拉取依赖。此步骤解决了离线归档在本机丢失 RepoDigest 引用的缺口。

使用同版应用标签运行 `check-gated-fleet.ps1`，本人第二台 Windows 电脑的随机专属项目
`gated-fleet-6d91ae8e296f4d898066b2882b81304c` **退出码 0**。真实临时 PG 的
Alembic 链升至 C01 且检查无待升级操作；结果 `gated-fleet.json` SHA256
`4ed5789be22f3aac7b0d7c063e637be39ba3ad65b953bcecee8a94fb47b40898`：
两冷库、三批、14 逻辑设备，7 条采集流有 C 持久数据、7 个执行器有实际执行 ACK
与后续降温采样、14 个设备离线后重新在线；设备运行时重启保留执行次数，C 历史恢复
并续收 7 流；两库同筛选快照 CSV 分别 81/57 行。三批高温候选经鉴权 **ASGI
TestClient** 送 B，不是实际 TCP 或真实通知。`cleanup.json` SHA256
`b4c7dc3a04dfb8c9083e41301f70591dc7154fa0104c17f8485eb14e4481e8ee`
记录 test_exit=0、owned_cleanup_ok=true；随后按该 Compose 标签独立查询，容器和网络均为 0。

接着运行 `check-gated-telemetry.ps1 -GuardedRecovery`。首跑项目
`gated-f30d2711406c48d5b2d0148213783f27` **退出码 1**，`runner.log`
SHA256 `a2297b050e42d162c02af37abcbbc607a31139161b75130718d954c823ec5586`。
失败在 `check-gated-telemetry.py:265` 的 `wait_for(cooled)`：已收到命令执行 ACK，
再等 25 秒仍没有满足“ACK/新规则以后且较基线降温 0.02°C”的 C 已存观测。
同一临时设备 SQLite 只读回看为执行次数 1、制冷开启、当前模拟温度约 9.677°C、
已生成 52 条采样；这只能证明设备侧运行，**不能证明后续 C 持久化检查点通过**。
为判断可重复性，在源码/镜像不变、资源清理后仅复跑一次：项目
`gated-08c280f2714e4728b4d66d48f83bee9f` 也 **退出码 1**，同样停在
`wait_for(cooled)`；`runner.log` SHA256
`051f3228cdacd87b305e7c05d4ab227717f16f153ef9f4b267831a05524c9934`。
第二次设备仍执行 1 次、降至约 9.680°C 并生成 52 条模拟采样。两次
`cleanup.json` 都记载 `owned_cleanup_ok=true`，其 SHA256 依次为
`fadbc175262226ca6e490e40970e46e1c067206d23c883421195c2158ba1cbc3`、
`5fb04d3d76c6caf30ab930b6782921ea5ff61a39df3603aa9885641e6187219d`；
按两个项目标签再次查容器和网络均为 0。临时原始日志及 SQLite 只留忽略的本机 `work/`，
不上传。停止重复运行，交 A 按此固定检查点分析；不改超时、断言或 C 身份门禁掩盖失败。

因此 C 第二机**已过默认 CMD/真实 HTTP 和 14 设备分段，未过 GuardedRecovery**。
普通用户浏览器登录/页面点击、两渠道终端实收、同一场景完整 E1、最终冻结/参赛签收
继续标为未测或未完成。上节“固定摘要引用缺失、后两段未运行”仅记录本次续验之前
的首段状态，以本节的后续实测为准。

## 2026-10-27 C 第二机同版制品接收与首段实测

本机独立干净检出、共同 main 均核为
`0e508d147517f81dfe2b9245c45c0c0e30076592`。已私下收到
`FreshLink-main0e-second-machine-candidate.zip`，包长 `715,730,853` 字节，
SHA256 `9be5310a1bbc919251f1adc0379d72a5952c33f39a688e53648e1c3e48405720`。
在新目录解包，逐件核对清单中 9 个文件的字节数及 SHA256，全部一致；
其中源码 ZIP SHA256 为 `c9730ff2e201820f71ce019fbc0462a8af287c8a566e0a0f29e770f9d06e3a88`，
应用镜像归档为 `f7a5e3d5221404ffa90b4ed8011947fb37bb445b1a4b54d89346e8e19bc0b806`，
依赖镜像归档为 `ed1f0cb65f854baae187cc09b452b048772e265f7842639cbfec783b7e6b585a`。
导入镜像后，应用镜像 ID 为
`sha256:c9a6ee33c6a89083c2efb1565b437d859d5069e8aa37f040f23be5972a385254`，
`linux/amd64` 和来源 label 均与上述 main 完整 SHA 一致。导入没有启动容器；
旧工作区、数据库和镜像标签未覆盖。

在此独立 C 电脑，用项目 `check-application-candidate.ps1` 对新专属项目/空库运行
**无应用源码挂载的默认 CMD 镜像与真实 HTTP** 验证，退出码 0。
证据 `application-http.json` SHA256
`1ae883954691d1bc8e5281beb39a92c89139b636810fcdd67a4acc6b9d43c16b`：
编译 HTML/JS、健康检查、匿名 401、管理员真实登录、注册 201/未知风险、
重复 409 且总数仍为一、保存详情均通过。Alembic 在本次临时 PG 中升至 C01。
`image-cleanup.json` SHA256
`7b1b05c7166b4e95f42944522a38e89ef265bab08d6700e6071bb5e40fc164aa`
记载 proof/cleanup 退出码均 0；再次按该专属 Compose project 查询容器和网络，
结果均为空。原本已有的 Docker 实例和数据未清理。

**首段当时的缺口（已由上节续验更新）：**导入的 Mosquitto 镜像 ID 确为
`sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408`，
但其 `RepoDigests=[]`，脚本要求的固定
`eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408`
引用在此 Docker 存储中 `image inspect` 返回 `No such image`。因此不能放宽身份检查
去报 fleet/guarded recovery 成功；当时仍待固定引用的安全取得方式。
普通用户浏览器登录、设备/历史同筛选 CSV、ACK 与追溯亦未在此机操作。
本首段仅证明 C 第二机同版制品与默认 CMD/HTTP 冒烟，不是完整第二机验收、
完整 E1 或两渠道实收。

## 2026-10-27 收件前交接检查点（历史状态，已由上节更新）

共同 main 与 C 本机新建的独立干净检出均为
`0e508d147517f81dfe2b9245c45c0c0e30076592`；原 C 分支与旧包未改。
[A 在 #6 指定](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6420809608)
的同版源码 ZIP 为 `3,273,358` 字节、SHA256
`c9730ff2e201820f71ce019fbc0462a8af287c8a566e0a0f29e770f9d06e3a88`；
应用镜像归档为 `540,475,904` 字节、SHA256
`f7a5e3d5221404ffa90b4ed8011947fb37bb445b1a4b54d89346e8e19bc0b806`；
目标镜像 ID 为 `sha256:c9a6ee33c6a89083c2efb1565b437d859d5069e8aa37f040f23be5972a385254`。
这些是 **A 提供的预期值**，C 机器尚未收到该批文件，不能记成 C 已验 hash 或镜像身份。
本机只读核查 Docker Linux engine `29.9.2`、缓存 `postgres:18`、无运行中容器；
目标应用镜像和固定 digest Mosquitto 未缓存。接收/依赖缺口已在
[#6 C 接收状态](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6433195778)
直接请求 A 私下交付。未下载、导入、启动或触碰旧 Docker 数据。

[A #7 同版记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7#issuecomment-6421017446)
报告其运行机默认 CMD HTTP、两库三批 14 设备分段与第二轮真实浏览器验证通过，
并保留首轮历史分页 `socket hang up`/worker 失败；这些属于 A 环境，不计作 C 第二机实测。
本机下一步在收到原件后逐件验长度/SHA256/ID/label，使用专属空库依次运行无源码挂载
默认 CMD、fleet 与 guarded recovery，检查清理；普通用户浏览器操作仍须另验。
两通知真实实收与同一场景完整 E1 仍未签收。

## 2026-10-26 第二机接收预检（未执行冻结同版复现）

本机是 C 使用的独立 Windows 电脑，系统版本 `10.0.26100.0`。在只读、
不改系统配置的核验中，已安装的 Docker Desktop CLI 回报 Docker `29.9.2`、
Compose `5.6.1`，引擎 `OSType=linux`；C 盘当时可用 `47,926,599,680` 字节。
普通受限执行会话无法直接运行该 CLI；从已安装的用户目录在允许的进程中只读核验成功。
这证明本机具备接收并尝试运行候选的环境，不证明应用已启动或通过第二机业务验收。

旧附件 `FreshLink-second-machine-candidate-96afaab8ef5a-812eea5.zip`
在本机存在，长度 `755,924,625` 字节；其名称指向旧 `96afaab8…` 候选，
不作为当前共同 main `0e508d147517f81dfe2b9245c45c0c0e30076592` 的同版制品，
也没有导入或覆盖现有实例。A 尚未在共同入口指定与最终冻结完整 SHA 对应的
源码 ZIP、镜像归档、manifest 和逐件 SHA256，故本轮没有对最终包执行校验、
导入、启动、登录、设备在线、采集/CSV、控制 ACK 或异常追溯；均为**未测**。
取得 A 的私下指定制品后，C 只在独立配置和隔离资源上逐项复现，并将失败及清理
记录在 E1 #6，不复制 A 的数据库、配置或秘密。

## 2026-10-26 夜间续证：受保护恢复发送已审，E1 仍未签收

共同 main 后续已升至 `0e508d147517f81dfe2b9245c45c0c0e30076592`。A 的
[PR #88](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/88)
精确头 `9e27e52879f1e0d725950edee4aa53971d97a048` 共 13 文件，已合入该 main；
C 已对完整范围提交[正式 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/88#pullrequestreview-5653861150)。
本机对 6 个 C 文件与原辅助 PR #87 逐字比对为零差异，C 生产子树/C01 对原 C
`121550c468520adbbe7f35a9e286304e915d4bec` 身份检查也为零差异；
PR #87 因被 #88 覆盖而关闭，未合并或删除分支。A 已将旧固定 SHA pin 更新为
上述 C 原头。C 原分支随后正常合入新 main，无强推；当前 PR #11 与 main 的
三点差异只剩本报告一份 C 文档，不再重复合入已发布的业务文件。

本人 Windows 项目隔离 Python 3.14.3 对 #88 精确源码运行相关 unittest 29 项，
**22 passed / 7 skipped / 0 failure**；跳过项需 PostgreSQL 条件，不能记为通过。
`git diff --check` 通过。同头七项适用 Actions 全部 success；
[Backend run 40340621960](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40340621960)
日志为 **1444 passed / 2 skipped / 182 warnings**。A 在 A65 记录的 73 项 PG 专项、
本地整仓 1446/0 和真实 MQTT/PG/TCP 独立恢复进程属于 A 的环境与证据，C 本机
没有重跑该完整链。代码合并不等于两渠道终端实收、完整 E1 或最终
第二机验收；已有 attempt 后新前沿仍可能保守 held。下一次采证须等 A 固定同版源码、
镜像和离线包校验值后，在本 Windows 第二机实际启动并记录登录、采集、CSV、控制
ACK 与清理；未跑的步骤继续标未测。

## 2026-10-26 当晚较早检查点（M03/E1 未签收）

共同 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5) 已关闭；
A 的 [PR #82](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/82)
当时已合入 main `296d4cb1a844b8eb9cd93d8506a142581921bda4`。这只更新当时共同源码基础，
不补写下列分段为完整 E1，也不代表两渠道终端实收或 M03 成品签收。

| 来源与精确版本 | 本人实际验证与可引用证据 | 尚未验证 |
| --- | --- | --- |
| C [Draft PR #87](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/87) `7b758c3936a4ac57f9ec3936b57e8767d88031bc`，基于已合 PR #82 源码，目标 main，差异只含 6 个 C 文件 | 同 Connection 完整恢复证据复核和 CAS 租约、事务提交后锁外发送端口及回归已普通上传。Windows 项目隔离 Python3.14.3 聚焦 unittest **48/48**；Ruff check/format、两生产文件 mypy strict、diff check 通过。六项适用 Actions 均 success；[Backend 40334108491](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40334108491) **1435 passed/2 skipped/182 warnings**，新增领取 2 项和严格 PG 候选文件 5 项实际执行。 | A 尚须独立审 PR #87，更新共享 `check-gated-telemetry.ps1` 的旧 C 源 SHA pin 并在新头重跑门禁；未接 A 无人值守调度或真实 B 恢复 HTTP，2 skipped 不计通过。PR 保持 Draft，不自行合 main。 |
| 本人 Windows 第二机**局部预检**，挂载上述 PR #87 源码 | 现有 Docker Linux server 29.9.2、Compose v5.6.1 已实际可用。唯一临时项目 `c87pg-92409dc0ce` 的 tmpfs PostgreSQL18 上，A04→A05→C01 空库升级成功；C `test_telemetry_gated_high_postgres_candidate.py` **5 passed/0 failed/0 skipped**。JUnit 仅本机保存、未上传，SHA256 `D090FD2C64AFEAE79BA8299BA361524EF0A00C7D715CDA041D2D598AD6663A62`（5 testcase、零失败/错误/跳过）。本次临时容器/网络已清理，测试用临时前端占位已移除。 | 运行时来自缓存 Python3.14.8 镜像 `sha256:67e2446f5680469f12806a03622d7f395d1977121ca962a234db0f33db63dd4f`，其来源标签是旧候选 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`；新源码以只读挂载覆盖。**不是**最终同版镜像、运行包、浏览器/MQTT/HTTP 或第二机完整复现。 |
| C/B 固定更正 JSON 在已合 A82 组合源 `2a3e2cf2b4ad83d226626acb4bcd358b27373ab5` | C wire 验证和 B `CandidateCreate` 解析后，双端摘要均为 `sha256:8c04fb17927df069a2a4bf02213f221a76af0fa62110313db07f34e566290dbf`；字段与失败边界已在 [B #8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6417741574) 本人确认。 | 这只是 DTO/摘要对拍；真实 B HTTP、同源重试、告警更正自动投递、SMTP/飞书终端实收仍未由此证明。 |

下节保留同日较早各提交的原始记录；其中“Docker 不可用”“PR #82 待修”等
只描述当时环境和头，不覆盖本检查点。完整 E1 继续按
[#6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6)
逐步实测，最终 M03 按 [#12](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/12)
由三人分别核本人证据后签收。

## 2026-10-26 当前 C 恢复修复与更正投递（E1/M03 未签收）

| 来源与精确版本 | 实际验证 | 尚未验证 |
| --- | --- | --- |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 恢复修复 `9ec008befb477e13cc9e9e8f60deeb2782269043` | 保存的 `raw_text` 严格解析并与 `raw_payload` 比较；同 admission 序号而 evidence revision 更新时，仅在完整原异常/窗口/前沿重验后续验。本人 Windows 隔离 Python3.14.3 相关 unittest **49/49**（SQLite/纯函数），Ruff 四文件、mypy 两生产文件通过。精确头六项适用 Actions 均成功；[Backend 40310929517](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40310929517) **1378 passed/2 skipped/182 warnings/91%**，日志显示 C 严格 PG 候选文件 5 项实际执行。 | 本人此轮无可用 Docker CLI，未运行 A [PR #82](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/82) 的 89 项叠加组合。PR #82 旧头 `13e88899bb7ab42163b336508f061ee34ce8f9a8` 尚未含此修复；[C 阻断审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/82#pullrequestreview-5651329999) 等待 A 新头与真实 PG/HTTP 复验。两渠道实收、完整 E1 和最终第二机仍未验证。整仓 2 skipped 不计通过。 |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) PG 测试门控 `7e59adf942212097ecb49ef8a2ee9c512dbc59f2` | 仅在项目 GitHub Actions 临时 development `db/app` 且现有 inbox PG 门控同时启用时，C 的 15 项随机私有 schema 严格 PG 用例才执行；本机仍须显式开关和专属 `test-db/freshlink_test/FreshLink-test`。本人本机默认运行 **15 跳过**，用模拟 URL 证明非测试目标在建连前拒绝、受控目标通过守卫但连接被拦截。精确头六类远端检查完成成功；[Backend 40273501906](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40273501906) 日志中该 C 文件 **13+2 个通过点、0 个该文件跳过**，整仓 **1377 passed/2 skipped/182 warnings/91%**。覆盖并发单赢家、持久重试同 wire、新 Engine/连接回读、事务回滚、固定快照/CSV 与鉴权 HTTP 候选。 | 这是 GitHub 临时 PostgreSQL 与模块独立路由；本人 Windows 此轮没有 Docker CLI，未在本机跑 PG。新 Engine/连接不是容器重建；`metadata.create_all` 在私有 schema 不等于正式迁移或主程序挂载。两通知实收、A 自动发送、完整 E1 和最终同版第二机仍未验；整仓剩余 2 skipped 不计通过。 |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) `095594fae995cbafd707f2d5c92f4252c13bf027`，业务提交 `92c47dc86d904891e34cf94419fd0e8526c6dfd9` | 更正专用持久租约、冻结 B wire 摘要、锁外回执与有界重试已上传；默认 `contract_confirmed=False`，不领取或联网。本人 Windows 隔离 Python3.14.3 同步共同 main 后相关 SQLite/模拟 B **47/47**；Ruff/mypy 通过。精确头六类 Actions 完成成功，[Backend 40257883476](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40257883476) **1362 passed/17 skipped/174 warnings/91%**。 | 本轮新增严格 PG 双 worker/重启同 wire 用例因专用门控未启用而未执行；本机当前无 Docker CLI。真实 B HTTP、A 调度、通知终端和完整 E1 均未验。17 项跳过不计通过。 |
| C 协助 B 正式审查缺口：[PR #83](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/83) `905e50f5ba79ca8f12c895fcbff27d73bd0a60c5`，基于 B80 `b07a5e3e91cc674b3b06469a35f7c130839f1982`，目标仅为 B 功能分支 | 原温度异常 kind/metric 约束、鉴权告警列表双 source ID 与负例已上传；本人本机 Python3.14.3 HTTP 生命周期 **12/12**、Ruff/mypy、有限文本安全扫描通过。精确头六类 Actions 成功：[Backend 40272165454](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40272165454) **1365 passed/15 skipped/174 warnings/92%**，更正 PG 文件 16 dots；[Playwright 40272165352](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40272165352) 四片 **26+26+26+25 passed**，告警列表输入为 Mock。 | 本人本机 PG/浏览器未跑；B/A 尚需审查并选择接入，PR80 当前头未直接改动，不能把 PR83 的绿灯写成 PR80 已修复。真实两渠道收件、C→B 自动更正和 E1 未通过。 |
| C 固定 [更正 wire JSON](examples/data-quality-correction-f03-wire-candidate.json) 与上述 B 候选模型 | 本人隔离 Python3.14.3 只读对拍：C 校验器与 B `CandidateCreate.model_validate`/`compute_content_digest` 均接受同一固定 JSON，得到同一完整摘要 `sha256:8c04fb17927df069a2a4bf02213f221a76af0fa62110313db07f34e566290dbf`；B 解析 kind=`data_quality`、metric=`temperature`，关联 ID 保留。 | 此项没有数据库、HTTP 或网络，不证明原异常存在、同仓同批、重放、告警恢复或通知实收。 |

PR #83 行中的“B/A 尚需审查”是该精确头首次记录时的状态；A 随后已正式批准
并把原作者提交整合进 PR #82 候选，但 B 原 PR #80 仍未因此自动更新。
各行属于不同分支/精确头，不能相加为一套同版端到端结果。下方 10 月 25 日
记录的“无新 admission 不续验”仅是当时版本的断言，已被本轮完整证据重验后的
合法 revision-only 续验取代；原负向与失败历史仍保留。C 模块、A/B
集成、B 两渠道实收及冻结版第二机验证仍按 [#9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9)、
[#6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6) 和各自原始证据逐项更新。

### 10 月 25 日旧轮次补充

原始观测复核后又补 `raw_text`/`raw_payload` 一致性拒绝；本后续增量在项目
Python3.14.8 定向 C unittest **7/7**、Ruff/mypy 通过，尚未重新跑完整本机
PG 1370 项；下节 JUnit SHA 只属于此前 `0a3e2dcd39dfa8b43ee76e149d45f5cbae335dad`。
更正当时仍 held/attempt0；该旧轮增量的远端检查在当时待核，当前
PR11 精确头与 E1 状态以上表及 GitHub 实时记录为准。

## 2026-10-25 C 更正原始观测复核增量（M03/E1 未签收）

在 C 更正 wire 只读准备中复核关联原温度异常的持久 raw/规范值/质量/采样
时刻和观测归属；原始观测或质量被改时拒绝生成拟投递正文，held/attempt0
不变。本人 Windows 项目 Python3.14.8/隔离 PG18、唯一 Alembic C01 的整仓
**1370 passed/0 failed/0 skipped/2 warnings/91%**；JUnit SHA256
`07c3731beca25bbf7dea028f5218334f19bdd8a40f8c54274f245eea64d8935c`，
随机项目容器/网络清理后均 0。定向 C 7/7、Ruff、mypy 通过；完整步骤及
初次只读缓存失败见 [C 测试证据](03-test-evidence.md)。此项仍无 C 更正领取、
B HTTP/回执、双渠道实收或完整 E1，当前远端 CI 待本增量上传后回读。

## 2026-10-25 C 当前恢复续验与更正映射证据（M03/E1 未签收）

| 范围 | 本人实际运行 | 尚未验证 |
| --- | --- | --- |
| 已推送 C [PR #11 `ae51bad7ad7cdfcc144806bf9dc18671ed9741e1`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，共同 main `36cdc449d6f6b8bdb50b8cd0e74f2bacf97b4546` | Windows 项目 Python3.14.8、隔离 PG18/tmpfs、唯一迁移至 C01/metadata check，完整 **1370 passed/0 skipped/91%**，JUnit SHA256 `58283e669a997c5110dd9de8be0f0ec5c19023f3f7bf565f6213d86512c1991e`；六项适用 CI 成功，Backend PR 合并测试引用 1354 passed/16 skipped/91%。B PR80 精确 DTO 对拍可解析且 wire digest 一致，但仅只读映射，C 更正仍 held/attempt0。 | B PR80 最终审查、C 更正领取/真实 HTTP、A 真实恢复自动发送、两通知终端及完整 E1。CI 的 16 跳过不算本机 PG 通过。 |
| C 新的持续接纳恢复前沿续验端口（本节版本以文件所在提交为准） | 同一 Windows 项目 Python3.14.8/隔离 PG18，定向新场景 **1 passed/0 skipped**，首轮缺湿度样本被正确拒绝后在新库补齐重跑；完整整仓 **1370 passed/0 failed/0 skipped/2 warnings/91%**，JUnit SHA256 `e5cb18bcb6e90188c2deb6f3bd9355befafacb7c29a1f161034b8b9371edd709`。按随机项目标签核验本轮容器/网络残留 0。续验只改 C 冻结前沿序号/修订，不改 F03 正文、ID、摘要、尝试历史。 | 本增量远端 CI、A 生产组合与持续 sender、B 恢复实际回执/更正、最终同版第二机及 E1；超过历史规则最大间隔或新增高温时保守 held，后续新恢复窗口策略待共同确定。 |

不同 SHA、机器和运行批次的测试条目不能求和为一套完整 E1。详细原始方法和失败记录见 [C 测试证据](03-test-evidence.md)，A/B 结果只按本人 GitHub 证据引用。

## 2026-10-25 此前收口证据核对（M03/E1 未签收）

| 来源与精确版本 | 实际证据 | 边界与下一步 |
| --- | --- | --- |
| C 恢复发前原始证据复核，基于 `be22199637aa6010b1cfe82940a106397c44e122` 的本轮 C 候选 | 本人 Windows 上以项目 Python 3.14.8、隔离 PostgreSQL 18 和唯一 C01 迁移链完整运行仓库测试脚本：**1353 passed/0 failed/0 skipped/2 warnings/92%**，JUnit SHA256 `2c0cacb3c83a9038cf28dc6fc04ae72176ef8eb0bd914ee13352630f3c093c27`；原高温及恢复样本原始值与归一化/质量不一致均保守 held；Ruff/mypy 通过，临时容器和网络清理后回读均 0。操作与失败前置见 [C 测试证据](03-test-evidence.md)。 | 仅是发前只读门禁与隔离测试；锁释放后仍有新接纳竞态，B F03 更正/恢复真实 HTTP、两通知终端及完整 E1 未通过。测试是在本次待提交工作树运行，不能归属此前 be221 已推头或 A PR #78。 |
| C `feature/c-data-quality-bootstrap` 业务提交 `5c41ae657f01504af35228a811bf382b1d3d1bae` | Windows Docker Desktop 29.9.2 中以项目 Python 3.14 镜像、隔离 PostgreSQL 18 临时库执行两个 C SQLite 模块与三个严格 PG 用例，共 **31 passed / 0 failed / 0 skipped**；同连接外层回滚、重放、坏证据、晚到 invalid/conflict 更正稳定 ID 均纳入。Ruff lint/format、三个生产模块 mypy 与差异检查通过。测试容器/网络已清理。C 本人 [#9 状态](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6395970009) 留有环境与命令边界。 | 此提交是 PR #11 已上传头 `a620761795f0c04096ce0c826fbe0e3ff5c8a9a0` 的业务祖先；31 项结果仍只对应 5c41。更正 outbox 为 `held_contract`，没有 B F03 接收、A 门内调用或自动恢复。宿主 Python 3.13 缺业务依赖，不能记作宿主测试通过。 |
| C 跨冷库归属冲突修复 [`894c76b87ee9ab0af1a9f3373ba9a0c8cfc7e993`](https://github.com/Cynthia060913/freshlink-zhikong-public/commit/894c76b87ee9ab0af1a9f3373ba9a0c8cfc7e993) | 本人 Windows 隔离 Python 3.13.9/SQLite 四组相关 unittest **4+25+13+5＝47/47 通过**；Ruff、差异与暂存文本安全检查通过。同头 [Backend run 40187429556](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40187429556) 在 GitHub 项目 Python 3.14 执行 **1225 passed/15 skipped/165 warnings/92%**；另五项适用检查 completed/success，见 [#9 记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6396856321)。 | 新冷库冲突回执、旧冷库原异常 held 更正及恢复阻断已覆盖；不把通用 Playwright/Compose 成功当成本机正式 PG、主程序页面或 E1。此头本人未重跑严格 PG，15 skipped 不计通过。 |
| C 分支普通合入新共同 main 的提交 `a620761795f0c04096ce0c826fbe0e3ff5c8a9a0` | main `7f16115c2b063ec0efe171418ca7e2a1e2ab6d4e`（已合并 PR #76），C 差异剩 11 个本角色文件、合并无冲突。本人同一 Windows 隔离环境重跑上述四组 **47/47 通过**；Ruff/差异/有限文本安全检查通过。同头 [Backend run 40189299528](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40189299528) 在云端项目 Python 3.14 执行 **1337 passed/15 skipped/174 warnings/92%**，其余五项适用检查成功。 | 该 47 项是新合并头的 SQLite 回归；它没有重做 5c41 的 Python 3.14/PG 专项 31 项，也不证明自动恢复、B F03 更正接收、真实通知或完整 E1。 |
| C 跨冷库归属冲突 PostgreSQL 定向回归（本分支 opt-in 用例） | 本人第二机 Docker Desktop/项目 Python 3.14/临时 PostgreSQL 18 用 `unittest` 实跑 **1/1 通过、0 跳过**，验证新仓回执、旧仓关联更正、恢复阻断、不可 claim 与重放。先前直接选 `pytest` 时因未建应用 `user` 表在全局夹具失败，目标用例未执行；随后使用该 C 文件原生 `unittest` 入口成功。每次随机 Compose 项目与 tmpfs 测试库，容器/网络清理退出码 0；细节见 [C 测试证据](03-test-evidence.md)。 | 此项不是完整 PG 并发/重启、正式迁移或 E1；未将第一次夹具错误隐作通过，也未修改正式测试门禁。 |
| C 共同基线同步 `9420c5c9c818b3892c687718061b5f1f4656a658`，报告头 `ecfc07bc6a7cbf31ab4c7066836a58fa17d84f5b` | 干净工作区将 5c41 普通合入共同 main `e20f553bb85a67ac0ec74579a9fcc238b5d427a8`；本次无冲突、未向 main 写入，已用 im1yt 凭据正常快进推送。后续 C 修复另在 894c76b。 | **合入后没有重跑原 31 项组合**；894c76b 的 47 项 SQLite 和远端整仓结果是另一次运行，不能写成 5c41 的重跑或同一组严格 PG。 |
| A main-facing [已合并 PR #76](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/76) 原精确头 `4dbf136bef902df3018ec322a0eeb18b9909a157`，main 合并提交 `7f16115c2b063ec0efe171418ca7e2a1e2ab6d4e` | A 在 [#6 分段记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6395119311) 报告真实 MQTT、四 CLI、C PG、TCP B、raw/CSV 和合成通知重启。C 曾错误地用不受支持的宿主 Python 3.13 判两个无括号多异常 `except` 为语法阻断；[Python 3.14 PEP 758](https://docs.python.org/3.14/whatsnew/3.14.html#pep-758-allow-except-and-except-expressions-without-brackets) 允许该写法，项目 `requires-python >=3.14`。A 在[项目 3.14.8 的两入口编译/合成回归](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/76#issuecomment-6395644724)实测 16 项通过；C 已撤回错误意见，并[对该精确 46 文件范围正式 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/76#pullrequestreview-5642525007)。 | A 的分段实跑源码 `ecf19d0…`、入口回归、PR 头和 main 合并提交分别记账；main 本次发布仍不含 C 5c41/894c76b 的新恢复端口/更正，不能因此宣称自动恢复或完整 E1。 |
| C 第二台 Windows 候选包应用 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`、工具 `812eea511c2ef307a8378e30b1e85e792c4fc3b2` | 本人独立核对私下收到的 ZIP 与 manifest 摘要；在隔离 `-RunFleet` 实跑 14 设备、7 采集/7 执行 ACK、控制后仿真降温、两库历史/CSV 76/56 行。完整摘要、清理和警告见 [#6 原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6394870061)。 | 这是旧候选分段证明，`notifications_sent=0`、`complete_e1=false`；不含新 runner 与自动恢复，不能拼成 PR #76 最终同版第二机或 M02/M05 签收。 |

B 最终恢复更正接口、两种独立渠道的授权与终端实收，A 同连接门禁接线、最终同版复验及可移交运行包仍待各负责人提供精确版本和实际证据。C 汇编只引用各自记录，不将不同 SHA、机器和测试批次求和成一套完整 E1。

## 2026-10-25 同版范围更新（M03/E1 仍未签收）

本轮 C [PR #11 精确 `6474de86a30da1ebe1c36716583d198bc94b5a01`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
新增详情接收证据的 UTC 混合精度排序回归，保留原始观测和重复/冲突结果。
[对应 Backend run 40166876095](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40166876095)
在云端 Python3.14.8/受控服务跑整仓 **1219通过、12跳过、165 warnings、
92%**；该测试文件 8 项实际列入执行，另五项适用 Actions 成功。
本人初次保存时 Codex 隔离 Python3.13 缺业务依赖，只完成 AST、Ruff、
差异和有限安全检查；随后在本人 Windows 用户会话以项目 Python3.14.3、
uv0.9.26 与原锁文件 `--frozen` 同步到工作区旁独立虚拟环境，以
`python -m unittest -q` 实跑 C HTTP/存储/历史/快照/质量 **64项通过、
0失败**，其中 HTTP 8项，均为临时 SQLite。**本机仍没有运行 PostgreSQL、
主程序或浏览器业务实测**。详情排序修复不是新增数据入库、
正式导出或 E1 闭环。

同一隔离环境又运行 B 历史 F02 与 C 多批/恢复规则、C 评价/恢复窗口/
恢复 outbox 五个模块，**51项通过、0失败/跳过**；两项 B/C 用例直接调用
B 规则服务，但 A 门禁为 SQLite 合成替身。两个本机分组共 **115个不同
单测**，不能当真实 PG 门禁、自动恢复或完整 E1。

C 辅助 B 的 [Draft PR #72 精确 `b4027b0cba293d3fb05262524397a3d7fd87ad35`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/72)
仅用合成传输复现单渠道通知扫描饥饿；[独立 Backend run 40115858437](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40115858437)
为 687通过/80 warnings/93%。这是**另一分支、另一基线**，不得与 C 的
1219 项相加，不代表 B 主 PR #10 已整合、实际 SMTP/飞书收件或恢复自动投递。
A 在 [E1 #6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6)
记录的真实 PG/MQTT/HTTP 分段证据按 A 精确组合来源保留，也不能与上述
独立结果拼成同版完整 E1。当前仍缺 B 最终头复审、两渠道终端实收、恢复
更正契约/自动发送、第二机同版业务及最终 M01—M05 材料签收。

2026-10-24 已核验过 Docker Linux 引擎；本轮 Codex 隔离身份的 PATH 仍无
Docker CLI，但在本人 Windows 用户会话通过 per-user 安装目录绝对路径只读核验
Client/Server 29.9.2 和 Compose 5.6.1，镜像及运行容器清单为空。没有收到
可独立核哈希的最终同版包，因此未在此机拉镜像、启动或登录应用；该信息已
回报 A [PR #61](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/61#issuecomment-6394180967)。

## 2026-10-24 C 恢复保守核对与第二机环境（M02/M03/E1 未签收）

C [PR #11 精确 `a30e8b608e5c8bfd4e8e579984a493665647d7cf`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
增加只读恢复新鲜度端口和严格 PG 测试：冻结版本与原异常/当前证据相符时仅
返回 `ready_at_check`，晚到非法接收或版本变化时保守 hold。端口没有自动发送，
没有在锁外消除新接纳竞态；B/A 的更正事件契约仍待确认。

[本头 Backend Actions 40096723151](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40096723151)
在 Python3.14.8/受控 PostgreSQL 执行 **1217 passed/12 skipped/161 warnings，
覆盖率92%**，同头其余五项适用检查 success。本人 Windows 仅 AST/差异检查；
Python3.13 环境未跑 PG 专项。第二台 Windows 已安装并实际验证 Docker Desktop
Linux amd64 引擎 29.9.2、Compose 5.6.1；没有收到最终同版离线包或在此机
运行/登录系统。A 的 PR #63/#64/#66 的正式审查与其分段实测分别按对应 PR/
[#6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6)
来源记录，不把 A 的 PG/MQTT 分段拼成 C 本机 E1。B 真实两渠道发送与收件、
恢复闭环、第二机业务复现和 M01—M05 成品仍未签收。

## 2026-10-24 C 多批同事务、真实 F02 与第二机预检（M03/E1 未签收）

C [PR #11 精确 `45c22e3ad7ede0d4fd02206b7b244285c284d3e8`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
以普通 merge 跟上共同 main `df3b3eda409924d05bb0b45c392d9041cd2611a9`，
本轮相对 main 仅 C 模块/测试/说明 8 文件。新增一条已存观测对应全部有效批次的
原子评估端口：先取 B 的历史 F02，随后在 A 同设备门禁/单事务内按批冻结高温
候选、风险与同一个评估任务。测试覆盖混合高温/正常、两个高温不同稳定来源 ID、
缺规则/错单位/缺湿度保守未知，以及第二个 outbox 写失败时全部回滚。修复一处
恢复集成测试夹具：F02 规则种类应为 `threshold_high`，F03 事件种类仍为
`temperature_high`；前一头的红灯不写成通过。

本人 Windows 项目隔离 Python3.13/SQLite 相关 unittest **37项、35通过/2跳过**
（实际 B 服务测试需要项目 Python3.14），Ruff、单文件 mypy、差异检查通过。
同一精确头 [Backend run 40074918444](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/40074918444)
在云端项目 Python3.14/受控 PG 执行 **1214 passed/12 skipped/161 warnings、
92% 覆盖率**，日志中 B 实际 F02 多批与恢复各 1 项、严格 PG 门禁文件
5 项均执行通过。Compose、archive、Playwright、pre-commit、Zizmor 同头适用
检查也 completed/success；通用 Playwright 不等于 C 页面实际操作。

第二机只读准备：这台电脑 PowerShell 7.6.5 运行项目环境检查，Docker CLI
`NOT FOUND`；Windows PowerShell 5.1 直接运行脚本受执行策略阻止，未改策略。
WSL 版本可读、状态及固件虚拟化/SLAT 未能核验。没有安装 Docker、收到 A 的
离线包或在第二电脑运行，**云端 PG 不能代本机 PG，更不能代第二机**。A
多批 caller、自动恢复/B 投递与双终端实收、完整 E1 仍待；本项是测试证据，
不是 M03、E1 或材料签收。

## 2026-10-23 C 真实 B 规则到高温门禁组合（M03 仍未签收）

C [PR #11 精确 `5ddca2a46bbea8ece72a777ef044fe31915d6a8f`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)
新增单批次历史 F02 选择→F03 高温正文→A 同设备证据门禁→C outbox/risk/job
的显式组合端口。本人 Windows 隔离 Python3.13.9：规则测试 14 项中
**13 通过/1 跳过**（B 服务使用 Python3.14 语法）；恢复 outbox **22/22**，
Ruff 和差异检查通过。远端该精确头 [Backend run 39900905629](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39900905629)
在项目 Python3.14/隔离 PostgreSQL 中 **1125 passed/12 skipped**，新增严格
PG 文件 `...` 三项实跑含真实 B 历史规则服务、A gate 与 C 高温写。
[严格 A/C run 39900905633](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39900905633)
另在受控环境 **1137 passed/0 skipped**，日志同一 PG 文件 `...`；七项适用
Actions 均 completed/success。首提交
`8d6026367119c79cae00bf5e1a4ab7dfe37563a3` 曾因新 PG 测试夹具先插
批次触发仓库外键失败（1124通过/12跳/1失败）；后续提交显式 flush 父行后
恢复 Backend 与严格联合检查通过，不隐去失败历史。两套检查计数独立，
不能相加当成新的测试数。

该实测是**受控远端测试库**，不是本人 Windows 本机 PG 或生产库；测试 A
门禁与 B 服务同版调用，但没有正式 worker 调度、B F03 实际接收、通知终端
实收、完整浏览器操作或第二机。A [PR #54 同精确头重批](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/54#pullrequestreview-5623882328)
仅批准唯一 C01 候选及 opt-in 接线进入团队发布审批，不代表已经合并或完成 E1。

## 2026-10-22 C 单批温度高同事务暂存（M03 仍未签收）

C 新增预选 F03 温度高正文在调用方事务中与风险/评估任务一并暂存的端口；
本人隔离 SQLite 该组 **8通过**，验证回滚、相同重试和异文冲突边界。
新增的严格 PG 第9项在本人环境跳过，待受控专属数据库实际执行。
没有在 A worker、真实 PostgreSQL 或 B HTTP 中运行本端口；C 自动恢复和
完整 E1 仍关闭/未验，不能把新增单测当作真实告警闭环。

## 2026-10-22 C01 非活动提案（M03 仍未签收）

C 十表迁移提案本机静态/模型一致测试 **3通过**；仅放在
`backend/migration_candidates`，父节点拟为 A05。没有活动 Alembic 后继、
真实 PG 空库与旧行升级、容器重启或生产数据迁移，不能计入正式持久化验收。

## 2026-10-22 评估标记同连接局部增量（M03 仍未签收）

C 评估状态/风险同连接写端口本机 SQLite **7通过**；完整 B 历史规则
选择、C outbox 冻结与 A 设备锁尚未在同一事务接线，不据此增加 E1 完成项。

## 2026-10-22 恢复窗口端口增量（M03 仍未签收）

C 恢复窗口新增调用方同连接入口，本机 SQLite 相关 `unittest` **23通过**；
严格 PG 文件本机 **8跳过**，新第8项真实 PG 提交/回滚待实测。本入口没有
挂 A 门禁、冻结 stamp、发送 B 或开启自动恢复；不能计入 E1。

## 2026-10-22 增量（M03 仍未签收）

C 已[按精确头审核 PR44 A04 共同候选](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/44#pullrequestreview-5607772122)，
审核范围不含后续 PR45—53、正式 C 迁移和 E1。PR44 适用 Actions 七项成功；
A 受控 PG/浏览器证据的具体范围见 PR44 A32，不能当作 C 本机运行。
C 新同连接观测入口本机隔离 Python3.13.9 `unittest` **13通过**，严格 PG
文件**7跳过**，Ruff 检查通过。新增第7项 PG 两连接提交/回滚用例尚待受控
Python3.14/PG 实际执行；旧 A31 的六项 PG 通过不覆盖它。正式 A 写门禁、
C 迁移/规则/恢复接线、两渠道实收和完整 E1 仍未签收。

初版核对日期：2026-10-17；最新补证：2026-10-21。**状态：M03 底稿，未签收**。本文件汇集三人已公开的版本化证据，不能把不同 PR/机器/数据库的结果相加，不能代 A/B 签字。最终报告须在同一发布 SHA、部署配置和第二台电脑上复核后重写实际结果；本文件不证明 E1 或比赛验收。

## 10 月 21 日当前精确头与未验范围

**晚间新增独立证据**：A [PR43 `a5f27e6ffe7d56844aa388dbedef2ba2a06ee9ee`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43)
的 [A31 原记录](https://github.com/Cynthia060913/freshlink-zhikong-public/blob/a5f27e6ffe7d56844aa388dbedef2ba2a06ee9ee/docs/modules/A/31-current-c-postgres-followup.md)
在其受控 Python3.14.8/PG18.6 READ COMMITTED 环境实际执行 C 第 6 项，
严格 PG 文件 **6/6 通过、0 跳过**，临时 schema 剩余 0；全应用 mypy
93 文件及 ty app 通过。C 本人未在本机复跑 PG，只[按该精确头批准默认未启用的
A24 候选范围](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/43#pullrequestreview-5598813842)。
A30 另记录同组合 MQTT→C 实际 PG 24 份业务观测、浏览器原文/固定快照与
24 行 CSV、控制 ACK 后仿真温度变化，使用候选 B01 + 显式 A/C 测试 DDL；
不是正式迁移或完整 E1。下表“第 6 项没有 PG 实跑”仅是 A31 发布前的历史
判断，不再作为当前阻塞。

C 本人 [PR11 代码提交 `09f1dd5a017fd8ac437e3af9f08e4a705a1d982a`](https://github.com/Cynthia060913/freshlink-zhikong-public/commit/09f1dd5a017fd8ac437e3af9f08e4a705a1d982a)
修正最新观测页在轮询暂停后不会自行变旧的问题：服务端年龄随成功响应后经过
时间增长，60 秒后保守显示未知；随后只接受显式 UTC 时间，不把本机解析的
无时区或非 UTC 时间用于正常风险。Node24 纯边界测试 **4/4**；首次提交远端
pre-commit 因新增测试的 Biome 排版失败，已在后续格式提交
`939916e80d33313faec4f2653fedc344af1acf06` 修正，该头及随后
`db0f0d459ad735479284575210500b70e6aeccf3` 的适用检查均已回读成功。
未做本人本机 Bun/Vite/Chromium 页面交互，不把 4 个纯测试写成
完整 UI 验收。

### 当日后续证据（覆盖下表的旧状态判断，不覆盖旧测试来源）

| 来源、精确版本 | 实际证据 | 未验收边界 |
| --- | --- | --- |
| C [PR #11 `bf991a0c610dd91cc89be950af15ae020f711bd5`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) | C 在 `24000425ace4c7e00110906dd2e9aaaddf4e8caf` 新增第 6 项严格 PG 恢复同事务回归；本人 Windows 隔离 Python3.13 相关 unittest **26通过/6 PG 跳过**，新增方法另以临时 SQLite 文件代码路径预检 1/1。`bf991a0…` 修正 SQLSTATE 类型判定，本人精确复现并消除该文件 mypy strict 错误，ty/Ruff及26项回归通过。[精确头后端 CI](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39543885351) **557通过/6跳过/115 warnings/93%**，五项适用 Actions 均成功。 | 第 6 项没有 PG 实跑；本机整应用 mypy 被项目 Python3.14 语法阻断，不能称整仓类型检查通过。候选表与正式迁移、自动恢复、E1 分开。 |
| A/B 交接修复 [PR #42 `6ed501f13fd668412ea999b618a97ba188f7db1e`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42) | B 在 [#8 明确交接](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6307252259) 并保护原 ACK 部分提交；A 原样保留其作者提交后补共同原异常行锁。A 隔离 Python3.14.8/PG18.6 [报告](https://github.com/Cynthia060913/freshlink-zhikong-public/blob/6ed501f13fd668412ea999b618a97ba188f7db1e/docs/modules/A/29-handed-off-alert-lifecycle-repair.md) **1071通过/0跳过/93%**，三种交错均实际看到 PG 锁等待且安全；同头普通 [Backend CI](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39539365989) 为 **1065通过/6跳过**，两环境不混计。C [按精确头批准交接范围](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/42#pullrequestreview-5594935429)，本人未重跑 PG/Chromium。 | 仍是 Draft 分层候选，尚未合入正式唯一链；其浏览器恢复为真实 HTTP 的**合成候选**，不是 C 自动触发。它不证明当前 C 第 6 项 PG、两渠道实收或完整 E1。 |
| B [PR #10 `049f1c50d2288c2726453c2c6189789e3aca0dc8`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) | B 首个飞书传输适配增量已上传，B 在 [#8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6307252259) 将其归为服务接受/模拟验证。A 对此精确头 [审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5594680226) 指出默认 opener 的 timeout 误判、原始异常/回显泄露 URL 或密钥、response.read 超时外抛三项，待 B 修并复测。 | 不把 PR42 的 B2 并发修复自动算入 B 当前 PR10 分支；飞书尚未获可放行审查、真实业务调度和终端实收。SMTP 与飞书两独立渠道仍未签收。 |

上表为不同分支/机器的阶段证据，不可相加成同版闭环。以下当日较早
`0f107…`“并发未修”和“没有真实 PG 回滚”文字只描述其记录时的旧精确头；
当前候选修复及其仍待发布的边界以本节为准。

A 新 [PR #40 `e69094ff2c2bf81790a7402befdd8b18066d7c3c`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40)
含 C `f895e95b1df51c2fa8943be6dbaaa99bbe9e32b1` 原样业务、A 共享根
修复与 SDK 更新。A 隔离 Python3.14.8/PG18.6 同版 **1058 passed、0 skipped**，
其中原有 C 5 项 PG 专项实际执行；适用 Actions success。C 对 A/C 接线范围
[精确头 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/40#pullrequestreview-5594554218)，
本人未重跑该 PG/浏览器环境。该轮实测来源见 A 的
`docs/modules/A/27-current-c-transaction-port-validation.md`。C 后续新增的
第 6 项恢复同事务 PG 用例只在本机 SQLite 代码路径预检通过，严格 PG
仍未执行；不能回填 A 已跑的五项统计。以下旧行保留当时精确头记录，
其中“没有真实 PG 回滚测试”仅描述该旧头当时的证据缺口。

| 来源、精确版本 | 可复核的实际结果 | 未完成/未测 |
| --- | --- | --- |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 业务 `6e2c1de71fb71c3bb67c9731a311c6d1729c62d8` → PG 测试 `3dd61a3291420e8435cae55b90619a86a463f06c` | C 的恢复证据核对和 F03 outbox 可由调用方同一事务执行，SAVEPOINT 分类重复/冲突；本人 Windows 隔离 Python3.13/SQLite 相关 **55/55**，Ruff/编译/diff 通过。业务头 [Backend run39483084679](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39483084679) **557 passed/4 skipped**；测试头 [run39483603059](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39483603059) **557 passed/5 skipped/115 warnings**，两头适用五项 Actions 成功。 | 新增严格 PG opt-in 用例在本人及通用 CI 都跳过；尚无 A 同设备锁/连续前沿、正式唯一迁移、真实 PG 回滚与并发验证。自动恢复及向 B 投递仍禁用；这不是 E1 或 M03 签收。 |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) `f0e31c0ca01362009903b8a18166720236eb62cb` → 排版修复 `9a121597a18293798ad63dc10b8e98287492c84b` | 新增按可信首次接收 UTC 的有范围最新已存观测候选和页面 10 秒轮询；重发不替换、空/失败/陈旧风险未知，历史/CSV 快照不变。本人 Windows 隔离 Python3.13 历史服务 **4/4**、Ruff/编译/diff 通过；项目 Python3.14 [Backend run39480816473](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39480816473) **554 passed/4 skipped/115 warnings**，修复头五项适用 Actions 成功。初始功能头 pre-commit 排版失败，已在修复头通过。 | HTTP 用例本机未跑；通用 Playwright 不测此新页面的交互。未挂共同根/导航、未做同版正式 PG/重启/浏览器刷新、多设备批次风险或 E1。 |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) `48a6f6ea1eb2fbfb5fc20c8753d70ea07c79ee0b` → `077cbef46a04155a7b2b1535e2ac5985af9fb423` | C 新增管理员专用、有界拒收审计候选及普通详情的复用计数/风险提醒；精确头 [Backend run39475822611](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39475822611) 与 [run39477013822](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39477013822) 各为 **552 passed/4 skipped**，warnings 分别 112、113；各头适用五项远端检查成功。C Windows 隔离 Python3.13 仅 Ruff/语法/diff，HTTP 用例因需 Python3.14 未在本机跑。用例使用隔离 SQLite 候选表/JWT，含匿名与普通用户拒绝、跨设备复用两侧审计、原文保留。 | 未挂主程序或完成正式 C PG 迁移/重启；UUID 审计游标不是固定快照；没有冷库级原文 ACL、审计页面或新增详情的实际浏览器交互。4 skipped 不算 PG。 |
| B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) `0f107299790aca50fea5349d85a02379b1d58efe` | B 告警列表/页面候选、同头远端检查已上传；C 对其早先 B2 [并发路径 REQUEST_CHANGES](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5586852909)，A 按其 [#8 记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6295656226) 在真实 PG 复现双 ACK 和恢复后迟到 ACK。C 不把 B/A 测试记作本人执行。 | 新 B 头仍未修该并发 P1；SMTP+飞书适配/许可/私密配置与两终端实收未通过，不能把 Mock 或页面当通知验收。 |
| A [PR #39](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/39) `a4af1500e3b50ae13bbaa2b961876da7c09d6c7a` | A 报告共享告警导航/权限、PG 和浏览器实测，精确来源见 [A #8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8#issuecomment-6295375983)。C 对 A-only 十文件做[限定 COMMENT review](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/39#pullrequestreview-5589912256)：B 模块文件与 B0f107 一致、Node 脚本语法和差异检查通过，精确头适用 Actions 成功。 | C 未复跑 A 的 PG/Chromium；合成告警/只读权限不证明管理员 ACK 并发安全、C 最新头在该组合运行、真实通知或完整 E1。 |

这些结果来自不同精确头。正式唯一迁移链、同版 PostgreSQL/页面/CSV/重启、14 设备/第二机、真实双通知与 E1 仍需逐项实测；M03 不据此签收。以下 10 月 20 日及更早章节保留为历史版本证据。

## 10 月 20 日晚间同版边界与交付准备（本次最新核对）

| 来源、精确版本 | 实际证据 | 仍未验证 |
| --- | --- | --- |
| C [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 业务 `7565fbd2863e6ff4d9bfe1ac1d798506f89c8390`、文档 `e6bb8ef25974cdc6c709811c6424302caa68cb99` | 跨设备/冷库重用接收 ID 时，两条证据链均保守阻断恢复；C 本机隔离 Python3.13/SQLite **39/39**，Ruff/format/diff 通过。业务头 [Backend run39428737160](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39428737160) 和文档头 [run39429549594](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39429549594) 均在项目 Python3.14 报 **550 passed/4 skipped/108 warnings**，五项适用检查成功。C HTTP 筛选回归先前 `4860d328650170907113f35de8ac9a3d496b58bd` 的 [run39427662361](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39427662361) 为 **549 passed/4 skipped**。 | 这些是 C 候选源码/远端通用检查，不是 C 本人同头真实 PG、自动恢复投递或完整 E1；4 skipped 不算通过。 |
| A 对 C `e6bb8ef25974cdc6c709811c6424302caa68cb99` 的独立 PG 增量复测 | A [在 C #9 的原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6292845397) 报告受控 PG18.6 独立 schema 中，四项坏接收防护和一项 JWT/SQLModel HTTP 筛选回归实际通过，0 失败/错误/跳过；C 源码与断言未改，只替换测试工厂；两个临时项目清理退出 0。 | 仅最新五项增量，不是 C 本人本机 PG、全套同版测试、正式 Alembic、浏览器或完整 E1。首次工具配置失败也在 A 原记录保留。 |
| A [PR #38](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/38) `6a804c972fed16dfa67d496015116b9f7753e420` | A 在 [E1 #6 原记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6292497922) 报告其受控 Python3.14.8/PG18.6/Chromium 的干净运行 `884fd66786f0f7560074e1630db3e41101e57a6a`：真实 MQTT→A 持久接纳→C PG→浏览器历史/原文/同快照 CSV，共 47 条、页面与 CSV ID 顺序相同；控制 ACK 后确有已存降温仿真样本。C [精确头源码复审批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/38#pullrequestreview-5586477440)，本人只运行该脚本 4 项 CSV + 2 项降温基线自测；该头五项 Actions 成功。 | A 实链固定 C `e8a08e62b5902f75ece8195ef44d740fc8995abb`、B `8561ce1ec0ea4e996bccbdc5f46d473bb170fc09`；C 未重跑其 PG/MQTT/浏览器，也不能外推 C 晚间 `7565fbd…` 或 B 新头。A/C 仅临时隔离候选 DDL，非正式唯一迁移链；没有两通知、连续自动恢复或完整 E1。 |
| B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) `470566fb9b840f7a172fc1fa7eac1ca0321615a2` | C [精确头安全返修复审批准](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5586394948)：测试库改随机本轮专用名，默认不启用危险 PG 测试，CI 显式启用；[Backend run39436721712](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39436721712) **644 passed/0 skipped/80 warnings**。 | C 本人没有 PG 重跑；共享 CI 工作流仍需 A 核对。PR 仍待正式整合，SMTP 实收及第二独立渠道未获证，不能据此签 E1。 |

此表按各自原始机器与精确 SHA 记账，**不把三个结果合并成同版通过**。下一次测试先由 A/B 发布审核后的唯一迁移链，再在包含最新 C 的同一组合复跑 PG、浏览器快照/CSV 和自动恢复；通知实收另由账号持有人提供脱敏证据。

## 10 月 20 日 C 新业务头与 A 同版候选（优先于下方历史记录）

| 来源与精确版本 | 本轮实际结果 | 未通过或未验证 |
| --- | --- | --- |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) `6110f3b3c611d6790fd1eb8be29dfbcf38a1fc0b` | Windows 项目隔离 Python3.13/SQLite 恢复相关 **38/38**，新增三项非法接收/冲突/ID 重用保守阻断；Ruff lint/format 和暂存差异检查通过。同头 Python3.14 [Backend run39423643885](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39423643885) 整仓 **548 passed/4 skipped/103 warnings**，五项适用 Actions 完成成功。 | 本头本人未在 PG、正式页面或 B 服务实跑；4 skipped 不计作 C 正式 PG。已存坏证据查询不能防 A 队列未提交旧消息和检查/冻结竞态，自动恢复/完整 E1 未验。 |
| A 对旧 C `e8a08e62b5902f75ece8195ef44d740fc8995abb` 的独立复测 | A 在 [C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6287388032) 记录遥测 192/192、把三项扫描测试的 SQLite 工厂换成受控 PG 后 3/3；A [PR #37](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/37) `3d52239f30fccb06f19e93947c0fcded429ed1fa` 保留旧 e8 的 11 个 C blob 字节一致，组合专项与容器重建结果按 A 的精确记录引用；C [限界复核](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/37#pullrequestreview-5585082483)。 | A 的旧 C 头和 A 的环境，不能作为新 611 的 PG/正式迁移或完整 E1。新 611 的坏证据阻断未进入 A 组合。 |
| A [PR #36](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/36) `3379c06a05828a47059844f8ce91c415782aa92f` | C [精确头 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/36#pullrequestreview-5583810362) 同设备未结前驱阻挡，六项该头 Actions 均成功；A 独立实际 PG 1001/0跳过按其 PR 说明。 | FIFO 不是连续 C 提交前沿；过期调用、后接纳旧时间、同刻 UUID 不同键、invalid/conflict 处理仍需同版复核。正式链 B01→A04→C 未发布。 |

最小前沿/保守不自动恢复提案已写入 [S0 #5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5#issuecomment-6291022753)，不重签已完成的 S0 最低出口。B PR #10 安全/PG 返修仍待作者新头，C 在其完成后精确复审；两渠道真实收件、正式页面/CSV、第二机和最终 M03 仍未签收。下方旧头与曾经红灯保持历史真实性，但“当前”均仅指当节记录的时间。

## 10 月 20 日最新 C 精确头与迁移顺序（优先于下方历史行）

| 来源 | 已验证结果 | 仍需实测 |
| --- | --- | --- |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 扫描业务 `d36a0aa54eee5a263c12e0a4025e1f412d63197d`、格式头 `78b98d8e700124ee55c7941813ec9e66557e3255` | 本人 Windows/Codex 隔离 Python3.13+SQLite 文件，恢复/outbox/扫描 **13/13**，包括分页后重开连接续扫、坏时间只重置一次、缺规则/缺表不推进；Ruff lint/format、语法及 diff 通过。同头[项目 Python3.14 Backend run39390566196](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39390566196) 整仓 **545 passed/4 skipped/103 warnings**，五项 Actions 成功。首 `d36a…` 的格式门禁红灯已在 `78b98…` 修正。 | 仅显式端口；无生产调度、跨进程锁、A inbox 晚提交补偿或本扫描同头真实 PG 专项。4 skipped 不算 PG；正式 C 迁移/主页面/B resolved/通知实收/E1 未验。 |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) 代码头 `15c87c66de0eec2578248ce21ca95fc928c97a8a` | B 历史规则查询超时、断线、权限暂不可用明确为可重试，evaluation 保持 pending、raw 与风险 unknown，不写 F03；远端同头[项目 Python3.14 Backend run39385744813](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39385744813) 实际执行新增回归，整仓 542 passed/4 skipped/103 warnings；五项 Actions 完成成功。本人 Windows 隔离 Python3.13 做改动文件语法及 diff 检查。 | 新增 SQLAlchemy 用例未在本人隔离环境运行；4 skipped 不证明 C 正式 PG，B 正式 HTTP 故障映射、持续扫描、正式迁移、主应用页面与完整 E1 未验。 |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) `e4ab1927a88e22af4517b5b0ab9f780dd3935bcd`，业务增量 `644e9dafe56ce05266a7a7494c6727fb0a1950ed` | Windows 隔离 Python3.13 纯处理子集 97/97；新恢复窗口可信接收水位的 SQLite 文件连接重开回归在[项目 Python3.14 Backend run39383941930](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39383941930) 实际收集并通过。该整仓 run 542 passed/4 skipped/103 warnings；精确 e4ab 的五项 Actions 成功。高温/原始观测不删除，非法时间仍未知。 | 4 skipped 不算 C 正式 PG；没有正式 C Alembic、跨窗口调度、真实数据库容器重启、C 页面浏览器交互、B 真实恢复或完整 E1。SQLite 连接重开不是容器重建。 |
| C 审查 A 六表 [PR #34](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/34#pullrequestreview-5581672494) `41b977170e49b530285d22774b6aae16697592c3` | 三文件实际复核，C 接受**候选**唯一序列 B01→A04→C 十表后继；A 本机受控 PG 994/0 的原始证据按 A 本人 #7/#5 保留。 | 该 PR 当前[远端 Backend run39382142884](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39382142884) 为 1 failed/989 passed/4 skipped，C 已 REQUEST_CHANGES；不得拿 A 本机 994/0 代替红灯或视为正式迁移发布。B PR #10 安全返修/最终头复审也未完成。 |

下方 `15c87…`、`e4ab…`、`bc71…`、`df232…` 等行保持各自日期和环境的历史真实性，**不是当前 C 业务 HEAD**，不得与新 545 项结果相加。完整 C 状态以 [#9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6263404899) 为准。

## 10 月 20 日 C 候选恢复接线与待验边界

| 来源 | 精确版本及实际结果 | 不可外推的范围 |
| --- | --- | --- |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) | `bc71b76368c69952657c318297452482f49881c0`（共同 main 基础 `e235a25650fce96b3ec92888e28a3d0702b16a06`）。Windows/Codex 隔离 Python 3.13 + SQLite 文件库，C 存储、连续恢复、F03、outbox、投递与规则相关 **71/71 OK**；新模块 Ruff lint/format、Mypy strict、Ty、暂存文本扫描通过。持久窗口重读观测证据后，原异常本地 `delivered` 才冻结稳定 ID 的恢复 F03；重开库再调用为 duplicate，变更历史规则或污染观测保持阻断。同 SHA 的五项远端 Actions 已 completed/success；[Backend run 39283815955](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39283815955) 是整仓 **488 passed / 4 skipped / 103 warnings、93% 覆盖率**。 | 这是**显式调用**的隔离端口和合成规则，不是自动调度、正式 C Alembic、同头真实 PostgreSQL 专项、B HTTP 接收/原告警 resolved 或 E1。Backend 数字为整仓，不是 C 专项；4 项跳过不计作实跑。 |
| C 本机扩展收集的环境限制 | 同头 `test_telemetry*.py` 通配运行**共 173 项，含 2 个导入错误、6 个环境跳过**：共同 main `backend/app/modules/iot/ingress.py:121` 的多异常省略括号是 [Python 3.14 合法语法](https://docs.python.org/3.14/whatsnew/3.14.html#pep-758-allow-except-and-except-expressions-without-brackets)，本机隔离 Python 3.13 无法解析；另一项因隔离环境缺 FastAPI。已在 [A #7 更正原误判](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7#issuecomment-6271798057)；无 A 源码修复请求。 | 173 项不可写成“全通过”；本机没有 Docker、psycopg 或真实 PG，不声称本人同头 PG/整仓验证。项目 3.14 CI 实跑结果见上行。 |
| A 对较早 C 头的独立 PG 复测 | A 在 [C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6270680816)报精确 C `78e61cd347646911b3e6a6c94d0998aa23545af4` 遥测 177/177、0 跳过，新增持久恢复进度 PG 探针 5/5。 | A 的受控环境和旧 C 头；不能挪为 `bc71…` 的同头 C 本人 PG 测试或完整自动恢复。 |
| B 迁移依赖 | B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) 当前 `2a5575923e765baf4134eabe25e3f73dbcff08be` 仍 draft、未合 main；A [审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5573636005)记录默认启用并强删固定 PG 测试库、密码 URL 导致两项 PG 误跳过等待修。 | 下方旧 B856/3f43 的测试、批准或候选迁移证据不能替代对 B 最终修复头的 C 复审和唯一链发布。 |

当前 [C #9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9#issuecomment-6263404899)保留未完成范围：正式 C 迁移、实际 A 观测与 B 历史规则自动恢复、主程序鉴权查询/CSV/页面、通知实收、E1、第二机和最终 M03。下方各节保持各自**当时**的版本/测试证据；表内写“当前”的旧记录只指其所在日期，不代表 10 月 20 日实时 HEAD。

## 10 月 19 日 C 代码实测头补证（优先于下方旧快照）

| 来源 | 已核实的版本和结果 | 限制与下一次实测 |
| --- | --- | --- |
| A 对修复后 C 的同版组合 | [A 在 E1 #6 的原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6265029865)：A `c15fe46663410fa4112a2767b503bef356814508` + B `3f43af70a29bfc0859fed711333cd38e7e505a05` + C **代码头** `df232b4ce2ada48fce266ed04fcf1071525d86ce`，隔离 Python3.14.8/PG18.6、Uvicorn/JWT/真实 B HTTP **33/33 通过**；此前摘要首失败解除，C 可按 B 回读确认摘要并持久标记 delivered。同精确 C 代码头遥测 **166/166、0 跳过**，其中 4 项严格 PG 专项实际运行。 | A 代跑并提供私有日志 SHA256；不是 C 本人 Windows PG 测试。使用候选 C DDL，没有正式 C Alembic、该探针内 MQTT/连续恢复调度、两通知或完整 E1；手工合成恢复不等于自动三样本恢复。后续文档头 `0dca6f2…` 仅改报告，业务树未改。 |
| B01 正式迁移候选 | B [PR #10 新 HEAD `8561ce1ec0ea4e996bccbdc5f46d473bb170fc09`](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) 增 `b01_alerts_storage`（`down_revision=a03_control_storage`）及 9 项迁移测试；[同头 Backend run 39238020710](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39238020710) 的 PG prestart 实际运行 a03→b01，整仓 596 passed。A 在 [PR #10 的独立记录](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#issuecomment-6265683392) 进一步报告精确 A+B856+Cdf 的 PG/HTTP 40/40、B 表由 Alembic 创建、PG 规则快照 UPDATE/DELETE 被拒。 | B01 仍在 draft PR，尚未合 main；C 仍用候选 DDL。C 已对新 B01 测试入口提交[需要修改的审查](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5572604584)：B 步测试须容纳合法 C 后继，PG 写入测试须限定专用测试库；正式 C 迁移待 B 修复审核并发布唯一 head。 |
| C 本人 [PR #11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11) | 分支 `feature/c-data-quality-bootstrap`，本地/远端完整 HEAD `df232b4ce2ada48fce266ed04fcf1071525d86ce`，已包含共同 main `e235a25650fce96b3ec92888e28a3d0702b16a06`，behind 0/ahead 78。本机 Windows/Codex 隔离 Python 3.13：F03 摘要/outbox/B HTTP **合成 39/39**、纯恢复窗口 **10/10**；Ruff、限定 Mypy/Ty、暂存差异检查通过。[同 HEAD Backend Actions run 39234760153](https://github.com/Cynthia060913/freshlink-zhikong-public/actions/runs/39234760153) 为整仓 **475 passed/4 skipped**，五项适用工作流均 completed/success。 | 39 与 10 是不同限定测试组，475 是整仓数，不能相加。此头未在 C 本机以 Python 3.14/真实 PG 复测；没有正式 C Alembic、根路由/页面接线、真实 B HTTP、自动恢复投递或 E1。旧 `be795f…` 的 A PG 154/154 只证明旧代码头。 |
| B [PR #10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10) | 当前 B HEAD `3f43af70a29bfc0859fed711333cd38e7e505a05`，C 对该头[源码和回归审查提交 APPROVE](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10#pullrequestreview-5569259588)。C 新普通候选摘要对齐其旧行兼容规则：HTTP 正文保留 `related_source_event_id:null`，摘要排除该空字段；非空恢复关联仍计入摘要。 | 审查批准不等于 PR 合并、正式 B 迁移、真实规则联调、告警持久处置或通知实收。B 迁移尚未进入 main，C 正式迁移必须接唯一已合并链。 |
| A 的三模块旧头探针 | [A 在 #7 的实际记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7#issuecomment-6262487834)：A `c15fe46663410fa4112a2767b503bef356814508` + B `3f43af70a29bfc0859fed711333cd38e7e505a05` + 旧 C `be795f248350a24d73a02d7689585009af61e003`，真实 PG18.6/Uvicorn/JWT/HTTP 33 检查中 **32 通过、1 失败**；首失败是普通候选摘要不匹配，C 保持 retry 未伪报送达。 | 当前 C `df232b4…` 已修本地规范化并有固定测试，但 A 尚未在该精确组合重跑；不能把旧 32/33 改写成 33/33。该探针使用候选 DDL，不是正式迁移或完整 E1。 |

10 月 19 日旧 C `df232b4…` 的恢复窗口判据以原异常规则显式提供的恢复阈值为前提，要求至少三条不同来源、质量 `ok`、严格有序的采样，单次间隔最多 90 秒；重复/迟到不凑数，缺测/冲突/再异常重置。该**当时的纯决策**尚未持久化或自动投递；10 月 20 日的独立持久窗口及显式 outbox 接线进度见上表，仍未令 B 告警自动 `resolved`。此前 C 对当时源树的有限模式安全扫描为 494 个文本文件、9 个二进制未作文本扫描、0 个模式命中；这不是当前树的全面凭据审计。下列早前条目保留原来源时间，不作为当前 HEAD 的同版验收。

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
