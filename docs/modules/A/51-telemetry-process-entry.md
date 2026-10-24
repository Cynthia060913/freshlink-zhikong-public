# A51：复用既有端口的持续遥测进程入口

这是一项普通候选，不是应用已部署、完整 E1 或自动恢复许可。依赖 A50/PR63；
原 PR63 精确 a7 保持不变供独立审查。本次不改 B/C 源码、迁移、原数据库或宿主 Python。

## 缺口与最小接法

已有 TelemetryReceiver、TelemetryDeliveryWorker、GatedHighWorker 都是真实端口，
但最后一个只有显式 tick，完整组合入口此前主要出现在验证脚本。现在复用原实现，
在模块 `app.modules.iot.telemetry_service` 提供三个独立进程角色：

- `receive`：原 MQTT QoS1 接收、可信 A 接纳后 ACK；回调不做 C 业务。
- `store`：原持久接纳队列认领 → 同 Connection C/A 门禁入库 → 原终态检查点。
- `evaluate`：每轮最多14个已有评价任务，按真实历史 B 规则和 C 原子多批端口处理。

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

在已经配置好的**隔离候选容器**中，三个进程分别运行：

```text
python -m app.modules.iot.telemetry_service --role receive --config /private/telemetry.json
python -m app.modules.iot.telemetry_service --role store --config /private/telemetry.json
python -m app.modules.iot.telemetry_service --role evaluate --config /private/telemetry.json
```

可加 `--max-seconds` 做有限时演练；不是严格的抢占截止，当前有界数据库操作会先返回。
这段说明不授权在真实数据库运行，也不代表 Compose/M02 已最终接线。

## 验证与剩余范围

新增配置/停止/失败重试脱敏、真实专属PG已验证/错epoch/未验证拒绝检查；并复用
原接收与真实 B/C 高温测试。第一次相关46项全部通过、0跳过；补充CLI调用后
最终 **47项全部通过、0跳过**（Python3.14.8/专属PG18），包含真实历史B规则的
高温任务经CLI入口冻结一条C候选、信号处理器恢复；测试仅把engine工厂换成
专属schema连接，未模拟B/C业务。库基线运行唯一C01 Alembic/model check通过；
场景本身仍是私有测试schema，不冒充生产DDL。Ruff/mypy/ty通过。
证据 `work/telemetry-service-a9dd82e6a6ad43719e607605fbaae307/`，退出0/清理0；
测试时源码尚未提交，后续提交不追改原记录为干净源码。未重跑不变的全套和前端。

B已提交事件的持续通知派发、C恢复发送前当前证据核验、C→B业务投递的持续调度、
最终启动包/进程监督和完整 E1 仍须接线。两种终端实收、第二机及M01—M05不在
本次入口测试的完成范围内；不因多了CLI就签收它们。
