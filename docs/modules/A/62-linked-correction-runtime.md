# A62：关联更正后台接线与真实 TCP 证据

当前改动属于 PR82 的集成候选。共同 main 的发布、候选代码、一次验证、完整 E1
和两渠道实收分别核验；不能按本页把候选直接写为正式成品。

## 实现与原作者来源

正常合入 C `a395bafae390bd0bd247fa6f33b2f0dc5474cb73` 的专用更正领取、独立
wire 摘要、回执和持久退避；按原作者三提交整合 PR83
`905e50f5ba79ca8f12c895fcbff27d73bd0a60c5` 的原异常限制与列表双 ID。C生产
子树/C01保持与a395一致；不改B/C本人分支，不创建平行迁移。

A 新增 `CorrectionDeliveryWorker`，使用现有设备/epoch配置，轮转发现每页≤14个
内部 `c-data-quality-correction/v0`、带原事件ID、已经到期的更正。发现事务先关闭，
再调用 C 的专用持久 claim/finish 和 B linked-correction HTTP 端口。租约、回执
和原文复核归C；A不把held简单改pending。损坏本地记录保留待查，轮转继续处理
其他来源；进程重启仍按C保存的退避和租约判断，不重复发送已delivered记录。

只有显式运行 `telemetry_service --role deliver-correction` 且提供
`--confirm-correction-contract` 才会启用此角色；启动仍需当前范围核验和本机私有
认证。标志不是生产部署或真实通知许可。普通应用启动、迁移、原四个遥测角色
均不会自动启用。默认未确认的worker不会访问数据库或网络。

## 已实际运行

精确干净组合 `9fedf0c462c87693697a991c5e97c0da0a428960`，专属PG18/唯一C01
升级与metadata check、内部MQTT、真实TCP API，通过以下过程：

1. 四个独立遥测进程接收、存储、评价、投递高温；raw和同筛选CSV实际回读。
2. 实际正常MQTT形成恢复窗口，准备进程重启续验；显式验证调用恢复HTTP，原异常
   在B回读为resolved。
3. MQTT重放原message_id但更改温度，C保存迟到冲突和held更正。
4. 新独立更正进程调用C专用端口，经真实TCP写入B，回读更正来源与原来源两个ID。
   C内部正文/hash未改，attempt=1，原异常仍resolved，状态历史没有增加错误迁移。

运行命令为项目根的现有Windows脚本 `check-gated-telemetry.ps1`，选择
`-LinkedCorrection` 及已核对的缓存应用镜像。脚本创建随机专属环境，只使用内部
网络、不自动拉取镜像、不连接原数据库，结束只清理本轮资源。

报告 `work/gated-d868179160f949e3a669823f344302e8/runtime-composition.json` 的
SHA256为 `bdc3195dbe2553f555f2913d1932a6a32cddcef344c3e85e47cb3eeaff07b46c`。
其中 source_dirty=false、actual_late_mqtt_correction_background_tcp=true、
notifications_sent=0、complete_e1=false。业务子进程全0；预期stale检查2、
未初始化拒绝1、API owner SIGTERM -15；forced为空。容器和网络独立回读均0。

同头组合专项69 passed/0跳过，含C严格PG、B更正/HTTP、A调度和端口检查。JUnit
`work/notify-subset-814b3af0f2344c018face5124026d9f5/current-c-b83.xml` SHA256为
`5f72f6a36dc140d8ddf92f4ec854847d18f2373da1134b43816287220b30b37c`。
这些是分段证据，不能把不同版本结果拼为最终冻结版通过。

## 后续补证与仍未通过

2026-10-26 晚已在 A 候选正常合入 C `5b8018e19fda01f44f1c6f51ad83d7a95b33b045`
和 B `d1f5359959423fa463cfdee5500c6b2486b39e9f`。C 原文一致性与合法等序号续验正式
修复已通过 A 真实 PG 组合 120 项/0 跳过及完整后端 1433 项/0 跳过、91%覆盖率。
新组合真实进程/更正/控制与十四设备证据及剩余出口见
`docs/submission/A64-INTEGRATION-CHECKPOINT.md`。下段为旧头缺陷经过，不能继续
当作 C 未上传修复；无人值守恢复发送仍是独立缺口。

真实多批次准备回归已保存：不同原异常写窗增加同设备revision，原有C续验拒绝
等admission序号，较早窗口可能持续held。私有容器提案仅放开该判断时，多批次
通过但坏原文负向失败；提案没有进入C生产源码。原窗口/原异常raw_text与
raw_payload一致性及合法revision-only续验须由C最小修复、重新独立审查。

无人值守恢复发送尚未完成，当前恢复HTTP仍由显式验证驱动；本次后台更正成功
不能代替该项。完整同版E1、真实SMTP与飞书手机实收、控制/页面/14设备联合验收、
最终同版第二机、M01—M05和实际参赛投递仍待对应证据及批准。
