# A1.3 / A2：持久软件仿真与控制执行

2026-10-12。独立 A 交付，基于 PR #14 的 e2fcb00df214141758f2d21d6d7d6d278cb014fb；共同 main 为 a99c2a091204012891b8fa0c80e10b63087c57c1。**不是已上线的控制 API、连续 MQTT 服务或 E1。** S0 的三方签收仍看 #5，不能由本代码替代。

## 实现和调用边界

`backend/app/modules/iot/simulator.py::SoftwarePlant` 是一库一测点一执行器的最小软件模型。使用项目已有 Pydantic 和 Python 标准库，不修改依赖锁、主 Compose、B/C 源码或 PostgreSQL 迁移。SQLite 文件仅属于仿真器的本地设备状态，不能替代主程序的 PostgreSQL 命令、心跳、告警或遥测表。

- 初始化需要独立状态文件、真实注册后分配的执行器/测点 UUID、有效冷库 UUID和带时区时间；隔离测试使用明确固定 UUID，不代表已在应用注册或绑定。
- `execute(payload: bytes, topic: str, now: datetime, retained=False, fail_switch=False)` 校验 schema 1.0、自己的命令主题、设备/仓库、参数、时区和有效期；只支持 `set_cooling` 和严格布尔 `enabled`，不执行 shell 或任意动作。
- 命令消息为 schema_version、command_id、device_id、warehouse_id、action、parameters、issued_at、expires_at；应用的 queued/sent 等数据库状态不塞进设备消息。有效期上限300秒、单条4096字节是本版工程限制，非赛事规定。UTC等价时刻规范后比较，同ID改内容为冲突。
- **执行、状态变化和原始 ACK 在同一 SQLite 事务中提交**。原始 ACK 跨进程保存；同ID同内容并发或重启后重发不重复执行，返回原ACK（即使此时已过期）。新过期命令不执行，开关故障注入返回failed；失败ACK的executed_at为null，不能编造执行时刻。失败后重试原ID仍返回原失败；重新尝试需由未来管理员流程明确创建新命令。
- `sample(now)` 将温湿度原始 JSON、message_id、模拟功率/电量和待投递记录一起保存。重发使用 `pending()` 中同一原包，不重造采样ID。满队列拒绝本次采样，不推进模型或丢弃旧包。新样本必须时间递增，单步不超过60秒。
- `confirm_storage(message_id, outcome=...)` 仅供可信服务调用，只接受C持久化的accepted_new/accepted_duplicate；PUBACK/失败/冲突不能清待投递记录。**本轮没有实际调用C，也没有清测试待投递。** 未来传输层必须认证服务返回，不开放任意客户端调用本方法。
- 仿真器状态身份不可在重启时悄悄换冷库/设备。最多1000待投递观测、1000已确认历史、10000命令记录；命令缓存不自动删除以免重执行，满时显式拒绝并要求操作员处理。是行数限制，不保证精确文件字节上限。SQLite忙/磁盘故障原样抛出，不能返回假成功。

这里只提供设备侧逻辑，**topic字符串并不证明认证**。未来需管理员认证的命令服务、真实设备注册/绑定校验、生产Broker ACL、非retained命令投递、服务端命令持久化/ACK匹配/超时审计等接线。当前测试账号只能用于隔离容器，不能用于生产配置。

## 仿真数据生成规则

模型为 `dT/dt = 0.002 × (22 − T) − 0.04 × cooling`（秒、°C），以线性方程精确解推进；默认初温10°C、湿度固定65%RH。基础功率200W，制冷开启功率1000W，电量按各状态持续时间积分为kWh。参数为自主定义的工程演示模型，**不是校准测量、苹果保鲜建议、真实节能率或品质预测**。开制冷降低后续模拟温度并增加模拟功率，不包装为“已证明节能”。

首次遥测只发送 temperature/°C、humidity/%RH；功率/电量单独保存在仿真器及测试证据中。C尚未确认power/energy业务支持，不能未经确认添加到正式业务观测。最小模型尚未扩展两库三批十四设备。

大于60秒的停止间隔目前明确拒绝，不自动生成缺失时段数据。连续运行器与显式恢复/数据缺口记录尚未交付；不能通过删除状态文件绕过重启去重。这是后续实现项，不是“所有停机恢复已完成”。

## 本轮实际验证

完整隔离PostgreSQL回归157项通过、总行覆盖94%，仿真器153行全部覆盖；新增37个仿真检查包含：开启/关闭的后续温度/功率/电量、跨重启原ACK和原采样包、并发重复只执行一次、冲突、过期、模拟故障、错误身份/主题/时间/参数、retained、容量、队列满、存储结果约束、数据库写失败事务回滚和已确认历史保留上限。第一次156项回归有1项测试预期错误（满命令缓存先于倒退时间检查），修正隔离用例后重新全量通过，没有跳过失败用例或降低90%门禁。

实际证据：本机私有 `work/a01-tests-20261012103008-34564/pytest.xml` 与htmlcov。Ruff、mypy（26个app源文件）、ty、Windows脚本解析通过。远端检查以PR当前HEAD为准，不能沿用PR14的绿色状态。

实际 MQTT 文件桥测试使用已验证官方 Mosquitto 摘要镜像、`--network none`、无宿主端口、一套新临时公开测试凭据和独立目录：

1. 认证controller发布非retained QoS1 command，认证actuator真实订阅并保存原字节。
2. 新Python进程读取真实接收包并执行，持久ACK和后续样本，不是手写成功回执。
3. actuator将执行ACK发布回Broker，controller真实订阅；sensor发布后续遥测，controller订阅；分别cmp逐字节比较。
4. 再经Broker投递相同命令，**新的Python容器/进程读取同一状态文件**，返回原ACK，执行次数保持1。
5. 验证制冷后模拟温度10.099040→10.080177°C、功率200→1000W，待投递观测仍有3条（PUBACK不冒充C存储成功）。真实UTC时钟、simulation speed=1；测试参数不作农艺依据。

随后补充并重新运行了命令权限反例：actuator自行发布commands、controller跨设备发布commands均实际收到MQTT5 PUBACK 135拒绝；完整控制往返、进程重启与数据变化再次通过。不能仅根据mosquitto_pub的零退出码认为授权成功。

本机私有证据：`work/a-simulator-control-60a983551625468cbee358fece7ad22f/`，包含真实command/ACK/telemetry接收字节、状态文件和结果。脱敏摘要在本目录 `evidence/a-simulator-control-20261012.json`。临时Broker/测试PG与网络已移除，实际应用和数据库数据保留。

**文件桥是明确受控的传输测试，不是常驻实时监听器。** 本轮未测试生产凭据/TLS、真实注册设备授权、公共控制API/页面、服务端ACK状态/超时、心跳离线/恢复、C入库、B告警/通知或完整E1。没有向邮箱/手机外发。

## 复现

在已有可用Docker和本地freshlink-app:p0镜像的工程根目录运行：

```powershell
powershell -NoProfile -File .\scripts\windows\check-a01-acceptance.ps1
powershell -NoProfile -File .\scripts\windows\check-simulator-control.ps1 -Image eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408
```

脚本不自动下载未知镜像、不清应用数据。Docker路径不能自动找到时传-DockerExecutable正确绝对路径。传输脚本每轮创建新的独立证据目录及Broker，成功/失败均仅清理本次标记的Broker；证据保留。不能把共享的测试密码用于运行交付。

## 三方下一步

- A：常驻可信MQTT运行器、心跳/离线恢复、服务端队列和控制记录/API/ACK/超时；S0确认后接真实B仓库和C入库，补自己的页面。
- B：继续PR10已有具体审查缺口/正式迁移、S0 F02/F03/F04与仓库政策确认；控制记录仍归A，B只读引用command_id，不从此SQLite读取或写自己的命令状态。
- C：继续PR11与S0 F01/F06确认、PR14交叉审查；真实温湿度样本可在接线时消费，功率/电量暂未正式扩展。收到controller订阅样本不是C持久化证据。
- 共享收尾：PR14与本分支是分开的交付；本PR以PR14分支为基底便于聚焦增量审查，不绕过共同main或授权合并。S0/#6/#12仍开放。
