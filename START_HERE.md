# 迁移候选优先入口

先完整读取 docs/team/PUBLIC-MIGRATION.md；STATE 尚未 ACTIVE 时，
不要依据下层旧仓库链接发布、发帖或宣称三方已经迁移。
准备保存代码前同时读取 docs/team/v4/10-凭据安全与公开前检查.md。
下文保留为源码快照的历史说明，实际新main/任务以迁移入口为准。

# 鲜链智控：今天从这里开始

## 2026-10-14 接续

共同代码基线 main `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813` 已发布。
仓库继续 Private；公开迁移待用户批准，旧历史中个人信息未因 HEAD 清除
而消失。B/C 最新状态见本仓库 AGENTS 当前段及 #5/#8/#9 的完整提交记录。

新增[心跳持久化候选](docs/modules/A/08-heartbeat-storage.md)：229项后端测试、
旧版合成记录升级和数据库容器重启通过；页面状态/事件已构建，浏览器待测。
未合入 main、未改应用数据库、未接生产 MQTT/B。S0/E1 不提前关闭；B 修接收
与规则边界，C 继续真实原始观测存储，不以接口争议暂停所有独立工作。

同日后续[MQTT 服务端心跳](docs/modules/A/09-heartbeat-mqtt-worker.md)隔离实链
通过，完整后端255项/95%；仍是本地候选，生产部署、浏览器及BC业务尚待验。

最新[控制持久化/API候选](docs/modules/A/10-control-storage.md)：全后端296项/
95%，临时库升级与命令/回执真实重启通过，B仓库未接入时控制创建默认503。
还没有MQTT主程序控制发送器或控制页面；不把保存queued当设备执行。

## 最新入口（2026-10-13）

- 已发布共同main：`0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813`，A PR14—17已独立审查、检查合格并顺序合入。[共同更新](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6128155438)。原a99是初始基线；下文待合描述只保留历史。
- 主分支push云端任务被GitHub账户付款/消费上限条件阻止，未分配runner，不能说main已云端测试通过。先本地隔离验收，不擅自付费或提高限额、不为红灯跳过检查。
- B的PR10有凭据字面量安全阻塞，需要持有人撤销、B清除代码和复查，不能合入或拷到主程序；不要分享Token或私密配置，也不要自行强推清历史。
- S0剩余接口小增量/统一样例和三方实际接入签收；C已接受固定查询快照，B仍需补规则开放版本切换、超时按源未找到仍同ID重试。主程序在线状态、C入库、B告警实收和E1仍未完成。
- A后续只读注册心跳入口见[心跳组件说明](docs/modules/A/06-heartbeat-policy.md)；DB读取不等于状态写入，不把设备列表unknown伪改在线。

## 当前入口（2026-10-12，覆盖下方历史状态）

- A最新设备侧已进入[连续运行阶段](docs/modules/A/05-continuous-runtime.md)：真实MQTT常驻采样、心跳发出、命令执行及重启原ACK；尚未接主程序在线状态、C存储或B告警。S0不能因此单方关闭。运行入口/无秘密示例在simulator/README.md，实际代码版本以A #7/对应PR为准。

- PR #4 已合并；共同基线 `a99c2a091204012891b8fa0c80e10b63087c57c1`，main 后端及浏览器检查通过。开工仍刷新远程核对，不重复等待 PR #4。
- 三人可并行做已明确边界的模块；共同契约确认、真实 MQTT 接线、E1 和完整参赛验收尚未完成。
- A 当前：[连续设备运行说明](docs/modules/A/05-continuous-runtime.md)和 [PR #16](https://github.com/Cynthia060913/freshlink-zhikong/pull/16)；它实现设备侧持续采集、心跳发送、命令和原回执重启恢复，尚未合并，也没有接主程序在线状态、C 入库或 B 告警。PR14→15→16 按依赖逐级审核；以 #7 和各 PR 当前完整 SHA/CI 为准。B 处理 PR #10 当前审查与远端检查问题；C 在 PR #11 推进正式存储、联调和数据材料。
- 最新共享状态：[S0 #5](https://github.com/Cynthia060913/freshlink-zhikong/issues/5)、[A #7](https://github.com/Cynthia060913/freshlink-zhikong/issues/7)、[B #8](https://github.com/Cynthia060913/freshlink-zhikong/issues/8)、[C #9](https://github.com/Cynthia060913/freshlink-zhikong/issues/9)。完整要求仍是 v4/09 与材料 #12。
- 下方 10 月 10—11 日记录为历史，不再表示“尚未发布基础”。不重复初始化、不覆盖旧成果、不泄露私密配置。

## 当前入口（2026-10-11）

本轮已核对 PR #4 的 9043582b6a260dfd23da680a7272a807bb9fff23：15成功/1跳过，仍等B交叉审查，尚未合并。实时版本以GitHub当前HEAD与同提交检查为准；下方584/待上传等为历史。本轮新增完整验收矩阵及对接补答将形成后续提交，不能沿用旧CI声称新HEAD已通过。S0/E1未完成。

- [完整版目标、比赛最低标准、通知/服务器/材料](docs/team/v4/09-完整目标与参赛验收矩阵.md)。这是三位共同必读，不是已完成证明。B/C在本人任务确认已读，后续按GitHub接续。

- [A 观测、事件、回执及查询边界答复](docs/modules/A/01-contract-proposal.md)（提案，待 B/C 确认）
- [参赛材料、原通知与发布门禁](docs/submission/README.md)（清单，不是已完成材料）
- [共同起点实时记录](https://github.com/Cynthia060913/freshlink-zhikong/issues/5#issuecomment-6106049888)

本轮新增文件在发布前仅本机可读；以 GitHub 最新提交/评论核验是否上传，不默认文件已进 main。

当前协作入口：[三人持续执行总任务](docs/team/v4/08-三人持续执行总任务.md)。B/C 一次接受完整角色任务，后续自主读取 GitHub 选择下一项，不需要 A 逐条派发。最新版本和阻塞以 S0 #5 与各角色任务、PR 实际证据为准。A 当前规范修复见 [待发布交接](docs/handoffs/2026-10-11-A01规范门禁修复待发布.md)；用户终端发布也在 fetch 阶段遇到 TLS 握手失败，尚未形成新提交或合并。

## 2026-10-10 最新入口：v4，以下旧 P0 步骤仅作历史参考

2026-10-11 验证更新：隔离后端 77 项、91% 覆盖率及前端 SDK/Biome/Vite/TypeScript 构建通过，证据与未测项见 [最新检查与待发布修复](docs/handoffs/2026-10-11-A01前后端检查通过与修复发布待执行.md)。修复尚未上传，PR #4 尚未合并 main；不重复已通过的前端安装，不勾 S0 完成。更新后的限定发布脚本默认只预检，明确 -Publish 才保存上传到现有功能分支，等待新 CI 和审查。

本日最新状态以 [GitHub 三人任务已建立交接](docs/handoffs/2026-10-10-GitHub三人任务已建立.md) 为准：A #7、B #8、C #9、S0 #5、E1 #6 已建立；A01/v4 已上传 PR #4，但 CI 待修复、尚未合并 main。旧文中的“未上传/未创建”是先前快照，不作为当前事实。B/C 可按 S0 做独立准备模块，正式表/迁移/跨模块接线等待共同基线和契约确认。每次 Agent 会话开始主动读取五个任务及相关 PR，不把 Git 拉取当作已读取任务评论。

用户要求十月底完成且本人没有每天 3—5 小时，采用三人小任务并行、共同契约、早联调，而不是全部开发压 A。角色仍为本机 A、另外两位 B/C。

先读：

- [v4 总方案](docs/team/v4/00-月底交付总方案.md)
- [GitHub 接入和交付](docs/team/v4/01-GitHub接入和交付.md)
- [B Agent 首轮与后续指令](docs/team/v4/02-B-Agent指令.md)
- [C Agent 首轮与后续指令](docs/team/v4/03-C-Agent指令.md)
- [A 与总验收](docs/team/v4/04-A与总验收清单.md)
- [新手操作与S0/E1两个检查点](docs/team/v4/05-新手操作与两个检查点.md)
- [按阶段可转发群消息](docs/team/v4/06-可转发群消息.md)
- [最新交接](docs/handoffs/2026-10-10-v4-低人工投入并行方案.md)

共同仓库 `https://github.com/Cynthia060913/freshlink-zhikong` 已建立并上传 P0。A01 设备功能已有实际检查，A01/v4 已上传到分支 feature/a-device-minimal（a1f49ad6be8ad3c2213ffe9209bd4d7296324970），PR #4 未合并；B/C 克隆 main 不能假定包含这些文件，暂可查看 PR #4 中的 v4 说明。B/C 本机环境、旧代码状态仍待各自确认。

首轮只做接入/成果保护、最小契约提案，以及 B 通知适配器/C 纯数据处理。当前设备注册/查询有检查，心跳/MQTT/控制/遥测/仓储/两通知/算法未验收。旧文中“团队仓库未建立”“设备均未实现”“截止只知11月”“按v3不变”不再作为当前事实。

---

## 历史 P0 安装说明（环境缺失时参考，不重复已完成安装）

日期：2026-10-10；阶段 P0（共同基础）。三台电脑都是 Windows；本对话用户已确定负责 A，另外两位负责 B/C，姓名未提供。比赛提交时间只知 11 月，具体日子待确认。

最新实施顺序和逐阶段验收见 [后续任务清单](docs/ROADMAP.md)，逐个任务的详细标准和 B/C 转发说明见 [完整执行计划](docs/EXECUTION_PLAN.md)。2026-10-10：A 本机启动和接口检查已通过；用户提供登录后 Dashboard 截图，确认实际网页登录成功。B/C 两台复现状态未知。下一步为团队仓库与公共契约准备，不重复首次安装。Agent 自身的 Docker 管道权限限制仍保留，用户终端执行成功不意味着 Agent 获得管道权限。

## 已准备与尚未完成

已取得并锁定官方 FastAPI 全栈模板提交，保留其许可证与原始说明；新增团队规则、公共接口草案、Windows 检查/启动/停止/验证脚本及本地 Compose 入口。

A 本机容器启动与模板登录已验证；冷库业务、设备、算法和两种告警均未验收。本包是基础源代码和运行准备，不是完成作品。

## 你和另外两位同学现在做相同的事

1. 保留 v3 分工：本机 A，另外两位 B/C。用户已将同一份启动包和角色说明发给另外两位。旧压缩包中的“角色未定”属于历史状态，以本说明和最新交接为准。
2. 解压到自己可写的短路径，例如 `D:\freshlink`，不要把三个人分别生成的代码互相覆盖。
3. 让各自 Agent 打开此项目，先读本文件和 `AGENTS.md`，运行只读环境检查：

   `powershell -File scripts/windows/check-environment.ps1`

4. 查看检查中的 Docker CLI、Compose、Linux 引擎是否可用。检查本身不安装软件、不更改系统设置、不发通知。
5. 环境缺失时按官方页面安装并启动 Docker Desktop，使用 Linux containers/WSL 2。WSL 的启用或更新可能需管理员操作和重启，先保存工作。不要关闭证书校验或防火墙来解决下载问题。
6. Docker 就绪后，让 Agent 执行：

   `powershell -File scripts/windows/start-local.ps1`

   首次需要联网下载基础镜像和依赖，脚本初始化独立本地密码并构建模板。失败保留日志，交给 Agent 分析，不重置数据库。
7. 运行：`powershell -File scripts/windows/smoke-test.ps1`。它验证网页、API、登录和当前用户，不测试任何竞赛业务。完成后打开 `http://localhost:18000`。账号为 `admin@example.com`，随机密码保存在本机 `.env.freshlink`，无需发到群里。

项目只绑定本机地址；其他同学各自运行自己的数据库。`stop-local.ps1` 停止容器但保留数据。不提供“一键删库”操作。

如 Windows 执行策略阻止脚本，提供错误给 Agent 检查；不要全局修改策略或直接下载不明修复脚本。

## 阶段完成标准

- 三台电脑分别完成环境检查。
- 至少一台成功启动模板，通过登录与网页检查；其他两台随后复现。
- 使用对应的 v3 角色文件，三人围绕同一个团队仓库开发。

本机原始源码目录保留上游 Git 历史，远程名为 `upstream`，它不是你们团队仓库。分发压缩包不包含 `.git`，解压后没有 Git 历史；原始提交记录见 `docs/UPSTREAM.md`。还没有创建或上传团队 GitHub 仓库。团队仓库建立前不要推送上游；不要三个人各自使用 GitHub 的模板按钮创建独立项目。确定共同仓库后再由三人分别获取同一仓库，不把三个副本各自初始化成互不关联的项目。

v3 原分工文件保存在 `docs/team/`，其中功能和材料归属保持不变。各自先检查环境；具体业务按 A/B/C 分开。最新状态见 `docs/handoffs/2026-10-10-P0-启动验证通过.md`，早期准备记录保留为历史。

## 给各自 Agent 的共同首轮任务

“我们正在开始鲜链智控，三台 Windows，发包同学负责 A，另外两位按收到的说明负责 B/C。请先读 START_HERE.md、AGENTS.md、我的角色文件和最新交接。只检查本机环境并帮助运行 P0 共同模板，不实现某个角色的全部业务。已有文件不要覆盖，缺依赖请列出；系统安装、重启、外部通知与发布需要相应授权。运行后报告实际通过项、失败项和下一步，不把模板登录当成冷链系统已经完成。”

## 官方安装和来源

- [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- [微软 WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)
- [FastAPI 官方模板](https://github.com/fastapi/full-stack-fastapi-template)

Python 3.14、Bun 和 PostgreSQL 在容器内使用；仅为 P0 跑模板，不必三人先手动统一系统 Python。后续编辑调试环境再按已锁版本处理。
