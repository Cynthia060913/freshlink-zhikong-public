# A51：复用既有端口的持续遥测进程入口

这是一项普通候选，不是应用已部署、完整 E1 或自动恢复许可。依赖 A50/PR63；
原 PR63 精确 a7 保持不变供独立审查。本次不改 B/C 源码、迁移、原数据库或宿主 Python。

## 缺口与最小接法

已有 TelemetryReceiver、TelemetryDeliveryWorker、GatedHighWorker 都是真实端口，
但最后一个只有显式 tick，完整组合入口此前主要出现在验证脚本。现在复用原实现，
在模块 `app.modules.iot.telemetry_service` 提供独立进程角色：

- `receive`：原 MQTT QoS1 接收、可信 A 接纳后 ACK；回调不做 C 业务。
- `store`：原持久接纳队列认领 → 同 Connection C/A 门禁入库 → 原终态检查点。
- `evaluate`：每轮最多14个已有评价任务，按真实历史 B 规则和 C 原子多批端口处理。
- `deliver-high`：只认领显式设备范围中已冻结的高温异常，经原C持久租约/摘要/
  不确定结果回读与原B认证HTTP端口持续投递；SQL先排除恢复事件和有原异常引用的行。
  不在本地读/认领事务中进行网络调用，不加新队列或改变C的原重试状态机。

不建第二套队列、协议、框架或数据库。网络异常/原始坏数据不改写成正常；未就绪
规则继续保留 pending。停止信号由现有 worker 或有界轮询处理；存储异常固定脱敏
日志并等下轮，不打印 SQL、密码、payload 或带值的验证异常。

## 操作边界

部署者须先完成唯一正式迁移、注册/绑定、真实 broker ACL 和显式 frontier 初始化。
入口只读核对当前设备是温度传感器、实际绑定一致、origin 已验证、epoch 精确匹配；
缺失/旧 epoch/未验证就拒绝，不 DDL、不生成 epoch、不清 gap、不启用恢复或通知。
运行时原接纳/存储/评价端口仍逐事务复核，启动核对不冒充永久授权。

配置使用 HeartbeatWorkerSettings 原字段，加 `frontier_epochs`（设备UUID→epochUUID），
设备范围必须完全一致、最多14个，额外字段拒绝。配置文件留部署者本地；密码只从
`FRESHLINK_TELEMETRY_MQTT_PASSWORD` 读取，不写 JSON、命令参数或日志。
接收角色才需要此密码。TLS 与隔离明文限制沿用原设置，不绕过证书。

在已经配置好的**隔离候选容器**中，各进程分别运行：

```text
python -m app.modules.iot.telemetry_service --role receive --config /private/telemetry.json
python -m app.modules.iot.telemetry_service --role store --config /private/telemetry.json
python -m app.modules.iot.telemetry_service --role evaluate --config /private/telemetry.json
python -m app.modules.iot.telemetry_service --role deliver-high --config /private/telemetry.json
```

可加 `--max-seconds` 做有限时演练；不是严格的抢占截止，当前有界数据库操作会先返回。
这段说明不授权在真实数据库运行，也不代表 Compose/M02 已最终接线。

`deliver-high`另从进程环境读取 `FRESHLINK_B_INTERNAL_ORIGIN`（默认同机回环
8000）和 `FRESHLINK_B_SERVICE_BEARER`；凭据缺失不启动，不写配置/日志/命令
参数。复用原HTTP适配器的拒绝重定向、认证、限时/响应上限及明文仅回环限制，
不绕过已有管理员写权限；凭据过期按原持久有界失败处理，不自动生成或共享Token。
目前B接收不自动触发外部通知，运行接线仍须核当时B版本；若将来接收会外发，则
真实演练前仍须具体发送许可。本次只在专属测试网络/进程内路由验证，不发真实渠道。

## 验证与剩余范围

新增配置/停止/失败重试脱敏、真实专属PG已验证/错epoch/未验证拒绝检查；并复用
原接收与真实 B/C 高温测试。第一次相关46项全部通过、0跳过；补充CLI调用后
最终 **47项全部通过、0跳过**（Python3.14.8/专属PG18），包含真实历史B规则的
高温任务经CLI入口冻结一条C候选、信号处理器恢复；测试仅把engine工厂换成
专属schema连接，未模拟B/C业务。库基线运行唯一C01 Alembic/model check通过；
场景本身仍是私有测试schema，不冒充生产DDL。Ruff/mypy/ty通过。
证据 `work/telemetry-service-a9dd82e6a6ad43719e607605fbaae307/`，退出0/清理0；
测试时源码尚未提交，后续提交不追改原记录为干净源码。未重跑不变的全套和前端。

B已提交事件的持续通知派发、C恢复发送前当前证据核验与恢复投递、
最终启动包/进程监督和完整 E1 仍须接线。两种终端实收、第二机及M01—M05不在
本次入口测试的完成范围内；不因多了CLI就签收它们。

## 24日云端全套失败与最小修复

原头f3的后端和严格PG云端检查失败，不能用前述47项代称全套通过。已取得严格PG
日志：完整运行为1242通过/1失败，失败是新日志断言捕获为空。在专属库中先导入
入口模块，再执行与迁移相同的 `fileConfig("alembic.ini")`，单独跑该测试，实际
复现完全相同的失败。迁移配置默认关闭已有日志器，导致测试顺序依赖。

修复仅让该单测使用独立的真实Logger并显式接入caplog，不依赖其他迁移测试的全局
日志状态；重试次数、固定错误日志与无SQL/秘密泄露断言全部保留。没有修改业务
入口、共享Alembic配置、门禁或跳过测试。相同复现命令已由失败变为1通过；原失败
证据保留 `work/logging-repro-737ba65c02e8440d91add483918ec307/`，修复单项证据
`work/logging-repro-1415998e8b304351971e72c65ff8f56d/`。完整回归/新头云端检查
须随后实际运行，不能在本段预填通过。

后续干净提交 `f13db5cbe747e374517cd547d882674a36de17ce` 的完整专属PG回归
实际1243项通过、0跳过、覆盖92%；原云端两项失败及该头所有适用检查已成功。
外层结果会话结束后另核XML/coverage与资源标签：本轮临时DB尚在，已确认唯一项目
标签后补清理，并核验无遗留；没有因此重跑整套或触及原数据库。

在此之后新增 `deliver-high` 接点的最终针对性55项通过、0跳过，含真实JWT和
FastAPI根路由/B服务提交、POST成功丢响应后回读、进程中断后租约过期认领且不重发、
无认证只持久重试、不接恢复/其他设备。B业务未Mock；HTTP运输使用进程内TestClient，
不是外部TCP/通知实收。证据 `work/telemetry-service-0ad99a72cb7a4bf0921cf79a42a3220b/`，
退出0/清理0，测试源码当时未提交。Ruff/mypy/ty通过；新增接点完整全套/新头云端
需要另行实际核验，不继承f13的全绿。
