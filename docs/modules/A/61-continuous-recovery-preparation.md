# A61：持续恢复准备循环（显式候选，不是恢复发送器）

本增量沿用 A60 及 C 精确 `0f346e9ec5be7ac3aef74fb114c1e87f98d9fa99`
的同 Connection 端口，不改 C 生产文件/迁移、B 契约或宿主环境。
目的仅是将人工逐次准备/续验变为显式后台循环；不因循环存在声称完整 E1。

## 行为与边界

- 每轮只读发现作用域内已 delivered 的原高温异常；每页至多14个原事件，
  按 UUID 轮转，避免一个 held 原事件阻塞其他设备/批次。不会任选第一批。
- 复用已审核的 `GatedRecoveryObservationWorker.advance_page`：每个原事件
  最多50个已存观测，C持久接收水位保存进度；进程重启不清空窗口。
- 已存在稳定恢复子事件时，在 A stream 锁内拒绝再次推进/改写窗口正文；
  两准备进程竞争也只能保留一个冻结正文，随后只续验前沿。
- 只对 pending/attempt0/无租约的恢复调用原 `refresh_once`；新正常接纳可
  重新冻结，新高温/原文损坏/不完整前沿继续 held。leased、已经重试、已投递
  工作不被重新领取或重置。无变化轮询不增加证据版本。
- `RecoveryPreparationWorker` 没有网络/通知端口，不做 claim、B HTTP 或
  delivered 更新；启动应用、迁移、导入模块不会自动启动本循环。
- 有界等待、连接/锁超时与 SIGINT/SIGTERM 停止沿用既有入口，错误日志不含
  SQL/私密配置/原始载荷。读取失败后后续 tick 重试，进度仍以数据库为准。

## 显式入口

仅对已审核迁移、显式初始化 epoch/设备绑定的候选环境，在 backend 目录使用
**项目隔离解释器**，不要使用宿主 D 盘 Python：

```text
python -m app.modules.iot.recovery_observation_service --config <本机私密作用域文件> --watch-preparation
```

需要有限时长时加 `--max-seconds 30`。配置只含已有设备/epoch作用域与MQTT
非秘密配置；不在命令中传密码或 Token。输出
`local_continuous_preparation_only_not_B_delivery` 只表明本地准备范围，
不授权任何恢复/更正/通知发送。通知运行入口与发送批准完全独立。

## 验证与未测

26日首轮工作树专项：原调用/入口及新轮询共 **36 passed / 0 skipped**，
包含11个新PG调度/CLI/负向/重启/双连接竞争用例。最初规范检查缺少测试标准
库导入已修正后通过；不是修改业务依赖或宿主包。Ruff、三生产文件mypy通过。
正式提交的完整回归与真实进程报告另在PR/#6记录，不把工作树结果冒充发布版。

沿用原隔离验证入口增加互斥模式：

```text
pwsh -File scripts/windows/check-gated-telemetry.ps1 -AppImage <已有缓存应用镜像> -RecoveryPreparationWatch
```

此模式应在专属内部网络/空PG中实际启动、停止并重启准备进程，接收实际新
MQTT后自动续验，再由既有**测试驱动**完成TCP B恢复/回读。循环本身不发送。
报告 `actual_preparation_watch_restart` 单列，不能称无人值守恢复发送。

待 C 更正专用领取/回执与 B 原异常约束/列表双 ID 修复审定后，A继续接最小
受控发送调度。两独立通知授权实收、同版完整业务/页面/14设备、最终第二机、
M01—M05及实际参赛投递仍需各自真实证明，不能由本准备循环代签。
