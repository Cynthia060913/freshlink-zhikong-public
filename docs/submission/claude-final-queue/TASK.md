# Claude 收尾队列：四项连续交付，只改八个文档

## 目标与固定起点

你是 A 委托的材料辅助，不是 A/B/C 业务模块接管者。唯一仓库为
`Cynthia060913/freshlink-zhikong-public`。

- 工作分支：`claude/submission-final-queue`。
- 软件来源：`bfb2c927f2a1a083ac21996844d38ac4eb4abbef`，A PR82 集成候选，**非已发布 main**。
- 本任务书提交只是该来源的文档增量，不代表功能冻结、E1或参赛材料已验收。
- Draft PR 的 base：`feature/a-recovery-continuation`；head：本工作分支。禁止以 main 为目标。
- 27日前必选/实链/第二机并冻结，28—29日材料复核尽早投递，最晚30日前实际投递。
  这些是队长计划，不是保证；20日E1延期保留。先完整再完善，不能删两通知等硬要求。

先检查 origin、分支、完整HEAD、工作区。若分支/起点不符或已有用户修改，保护成果并报告，
不 reset、不 clean、不强推、不自动 stash，不把旧分支历史合入本分支。

## 本轮允许写入的完整清单

仅可新建/修改同目录以下八个UTF-8 Markdown文件；本 TASK.md 保持只读。

1. `01-REQUIREMENTS-AND-DELIVERABLES.md`
2. `02-VERIFIED-TEST-CHAPTER.md`
3. `03-USER-OPERATION-CARDS.md`
4. `04-RUN-AND-SECOND-MACHINE.md`
5. `05-DEMO-RUNSHEET.md`
6. `06-NARRATION-DRAFT.md`
7. `07-PACKAGE-REVIEW.md`
8. `08-HANDOVER.md`

禁止修改业务/测试代码、契约、迁移、根路由、SDK、前端、已有其他文档、配置、依赖锁、
CI或系统设置。禁止生成/改写任何验收通过JSON；禁止给别人签收。代码问题记录交A，不修。
不要另建PPT/图片/视频，也不要复制旧PPT当最终稿。本轮产物就是可汇编的文字材料。

四项按下面顺序连续完成，每项独立提交；全部完成后只建/更新一个Draft PR，一次性交接。
无需做完第一项就问用户下一项。总文字建议控制在约600行以内，避免重复历史与安全规则。

## 只读资料与当前事实

必读当前 `AGENTS.md`、`docs/team/PUBLIC-MIGRATION.md`、
`docs/team/DELIVERY-CHECKPOINTS.md`、`docs/team/FINAL-WEEK-PLAN.md`、
`docs/team/v4/09-完整目标与参赛验收矩阵.md`、`docs/submission/README.md`。
技术来源包括 `docs/submission/A-SYSTEM-DESIGN.md`、`A-TEST-REPORT-CANDIDATE.md`、
`APPLICATION-CANDIDATE-BUILD.md`、`SOURCE-CANDIDATE-EXPORT.md`、`design/`，
以及 `docs/modules/A/62-linked-correction-runtime.md`。

既有Claude成果优先复用，不从零再编一套：

- PR69：`claude/delivery-runbook`，此前84232d1af3041dfc9fe7320fe09d79b221afe378，
  `docs/submission/claude/USER-QUICKSTART.md`、`SECOND-MACHINE-ACCEPTANCE.md`。
- PR70：`claude/demo-script`，此前b9d032702e914b97abad5a580d8f1ec56a0b98de，
  `DEMO-SCRIPT.md`、`CAPTURE-PRIVACY.md`。
- PR71：`claude/source-handover`，此前6d32984edfea71e082d499592feb787abf7b0c2b，
  `SOURCE-PACKAGE-NOTES.md`、`MATERIALS-HANDOVER.md`。
- PR77：`claude/a-delivery-materials`，A最近回读头9e9ba3a6ef7c4d7db0c83ac98d7caaf8296e202d，
  `docs/submission/claude-a-materials/` 的技术章节、讲稿和来源表。

这些SHA是带日期线索；若使用旧资料，标注取到的精确版本。可以安全fetch只读引用，不能merge。
网络有限重试，取不到就记录“未取得”，继续当前源码能支撑的工作，不无限等待、不编造旧内容。

本机A证据根目录只读许可限定为：
`D:\codex‘s item（i create）\2026-09-26\2026-09-14-11-27-https\outputs\freshlink-public\work\recovery-caller-integration\work\`

只允许读取下列明确的合成验收报告，不扫描该目录其他文件/日志/真实配置：

| 相对证据路径 | 来源/结果与边界 |
|---|---|
| `gated-40c012abee254ec4b5ace0dbf4cb8bac/runtime-composition.json` | bfb干净源：MQTT/PG/TCP、更正后台、控制ACK及后续数据；complete_e1=false、通知0 |
| 同目录 `runtime-child-cleanup.json`、`cleanup.json` | 仅本次测试退出/清理，不等于其他机器 |
| `notify-subset-814b3af0f2344c018face5124026d9f5/current-c-b83.xml` | 9fedf0c462c87693697a991c5e97c0da0a428960组合69过/0跳过，不能改写成bfb全套回归 |
| `source-candidates/20261026162745-1b4fc0b5ff0d43fab7d08eb7245ab63f/application-build.json` | bfb真实前端编译及镜像源审计，165后端文件/39前端产物；不代表E1 |
| `image-http-20261026163228-b15f1c9793ef/application-http.json`、`image-cleanup.json` | bfb镜像默认CMD、无源码挂载，真实HTTP健康/登录/注册/重复拒绝/详情通过；不是第二机 |

若证据不在你电脑上或无法读取，只引用本任务书给定层级，标“未独立读取”，不假称实读。
不能把A私有work路径写成评委拿到包后也能访问的依赖；公开交接只保留脱敏证据说明。

必须保留的未完成事实：

- S0最小出口已完成，不重开整个E1作为S0前置。
- C多批次合法revision-only续验与保存原文一致性仍待正式修复；A私有提案89项通过
  只是提案验证，未进入C正式源，不能写“缺陷已关闭”。#5评论6409087579有请求。
- 当前恢复HTTP仍由显式验证驱动；更正后台已实跑不代表无人值守恢复发送完成。
- 控制实测10.002809→9.980946°C是短时软件仿真，只证明ACK后数据变化，
  不能称实物制冷、已降到恢复阈值，不能重排成“控制导致本次恢复”的因果证据。
- SMTP/飞书真实配置、接收同意、发送许可、服务接受和终端实收分别核验，目前未实收。
  GitHub发给学生的通知邮件不算项目SMTP告警实收。
- 最终同版第二机、同版完整页面/14设备联合验收、M01—M05最终签收与实际投递仍待。
- PR11目前已无文本合并冲突但仍Draft；mergeable不等于允许合并或E1完成。

## 任务一：M03需求与真实测试章节（文件01、02）

01按赛事全部硬要求及M01—M05列出：需求→实际模块/页面或API→所需证明→当前层级→
缺口及负责人。两库/三批同品种苹果/14逻辑设备不删；算法非强制，不为加分扩范围。
不存在的功能、页面、操作入口明确标记，不依据设计稿写成实现。

02写可直接供B汇编的测试章节，逐条写源码SHA、环境、输入/步骤、预期、实际、证据、
未测项。先写本任务书五组已证结果，再列失败/未完成。区分源码、Mock、PG、真实HTTP、
进程、浏览器、终端实收、第二机、完整E1。不要相加不同版本测试数，也不填最终通过结论。
用自然具体的项目语言，不编造纯人工经历、用户试用、收件或实验。

## 任务二：真实界面操作卡及运行交接（文件03、04）

03只读 `frontend/src/features/{devices,telemetry,alerts}/` 与实际路由，整理8—10张操作卡。
每张包括前置身份/数据、页面路径、源码真实按钮/字段名称、点击步骤、预期可见结果、
截图建议、对应源文件。未经浏览器实跑写“按源码核对/待现场验证”。
必须检查：管理员与普通用户、设备注册/绑定/在线状态、制冷命令与ACK、历史过滤/CSV、
告警确认及关联原事件。不存在仓库/批次页面就说明由现有API/初始化准备，不能编造页面。
禁用ACK按钮不能写“再点一次验证幂等”；后台幂等证明与界面操作分开。

04复用旧运行说明，分开发方与接收方：有Git检出可导出源码，拿到包的人不一定有Git。
逐项核对现有Windows脚本参数、工作目录、镜像导入/启动/停止的实际来源。只写已有命令，
不要自行改脚本或运行部署。普通start-local固定项目名freshlink，不能称随机隔离环境；
先核已有实例，不能删除/覆盖原数据。Docker安装/系统改动需机器持有人决定，Agent不执行。
第二机记录必须由真正另一台电脑的执行者填写；同机新容器/新Engine不能冒充第二机。

## 任务三：可录制演示脚本与讲稿（文件05、06）

复用旧视频脚本/讲稿，按实际界面组织约4—6分钟建议流程；这是内部建议，不称赛事强制时长。
05每个镜头列画面、操作、应出现的结果、来源、目前能否录、缺口及负责人。
必须覆盖设备/数据清洗历史CSV/告警确认/控制ACK后数据/恢复追溯/两通知。
未完成镜头标“待验证后录制”，不能用Mock、静态图或GitHub邮件替代真实结果。
通知镜头不展示账号、授权码、Webhook、完整收件地址或个人桌面。

06给出对应自然中文讲稿，术语和05一致；当前事实与待验证宣传分开，
不写准确率/延迟等没有实测的数据。不要制作假视频、截取他人项目、虚构使用者反馈。

## 任务四：最终包复核与一次性交接（文件07、08）

07按M01源码/M02应用/M03设计/M04可编辑PPT/M05实际视频，给可操作的包目录与复核表。
引用现有导出/镜像/材料检查工具，说明检查器只是有限一致性/安全检查，不证明材料真实性。
镜像ID与构建报告不是可运行交付物，实际交付还需镜像归档或获准获取途径及配置/启动说明。
正式外层包名仍保留赛事要求的赛道号_学号_姓名_项目名称；身份由本人提供，不能编造，
也不能为公开脱敏删除正式投递必需身份。公开示例用明显占位符，不写真实身份或收件地址。

08一次性交接：八文件路径、每项commit、来源SHA、实际检查、未读证据、待A/B/C补齐项。
列出“只差填真实结果”的明确位置，不把空表称成品。最终发送只由本人或具体批准后执行。
不生成看似已通过的验收记录，不发送邮件/飞书、不提交参赛。

## 验证、保存和停止

- 每项检查引用路径真实存在、按钮名称与源码一致、参数与脚本一致、相对链接可解析。
  八个文档都不得把绝对私有work路径当运行依赖，不扫描其他个人文件。
- 不运行全套业务测试/浏览器/数据库来验证纯文档；不安装依赖、uv同步、清缓存或改网络。
  不运行D盘宿主Python。若已有明确隔离runtime，可用现有文本安全脚本；没有就用PowerShell
  做限定文本检查并如实标未跑，不为检查工具安装新环境。
- 每项只暂存其两个允许文件，检查 `git diff --cached --name-only`，再单独commit。
  禁止 `git add .`。全部完成后核对相对软件来源bfb的改动只能为TASK及八个文档。
- 正常push到 `claude/submission-final-queue`；安全认证缺失时不索要Token、不改凭据。
  保存本地commit，报告完整SHA和具体网络/权限阻断。遇到写入响应不明先回查防重复。
- 有现成授权时只建/更新一个Draft PR：base `feature/a-recovery-continuation`，head本分支。
  正文列软件来源bfb、八文件范围、四项提交、实际检查、未测；不能以main为目标或自行合并。
- 四项完成即停止修改，等A逐项复核。A会核对范围/事实/证据，必要时要求返修；
  合格后通过明确审查及许可接入主线。本包完成不代表业务项目完成。
