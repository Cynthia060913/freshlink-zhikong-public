# A52：原控制模块的独立进程入口

依赖PR64精确ff3，不改变它的六文件范围或已请求审查的提交。本次在后续独立分支
保护增量，不改B/C源码、原ControlWorker状态机、数据库迁移或宿主Python。

## 最小实现

`app.modules.iot.control_service` 只负责读取原 `ControlWorkerSettings`、检查设备
是已注册并绑定真实B仓库的执行器，接原 `warehouse_lookup_for_session` 后启动
原ControlWorker。没有默认True仓库、手工ACK、导入即启动、DDL或数据重置。
配置限制64KiB、有限正运行时间、原TLS/隔离明文/ACL范围限制保持；原密码只从
`FRESHLINK_CONTROL_MQTT_PASSWORD`进程环境读取，不进JSON/命令参数/公开日志。
错误不输出带值的验证异常，停止信号与引擎清理使用finally，原信号处理器恢复。

在已经明确配置、获准启动的候选容器中：

```text
python -m app.modules.iot.control_service --config /private/control.json
```

此命令不是让同学在宿主Python装依赖，也不授权操作原数据库/真实设备。
最终M02需要把这些入口与已审版本组合，不能把本页说明当成可运行成品签收。

## 实际验证

- 缓存Python3.14.8/随机专属PG18，入口8项＋原控制44项共 **52项通过、0跳过**；
  含原桥接工厂、有限时间、坏配置/缺密码脱敏、范围失败不运行及清理/信号恢复。
  基线唯一C01 Alembic/model check通过。证据
  `work/control-service-45123891b62c4cf39ae3aaa4e6fdd95b/`，退出0/清理0。
- `check-gated-telemetry.ps1 -ControlEntry`新增显式选择；原默认对象方式不变。
  本次真实启动Python子进程（不是MagicMock），连接认证Mosquitto/专属PG，经实际
  B仓库绑定和根控制API创建命令，模拟执行器只执行1次、产生executed ACK，C保留
  ACK后新降温观测，JWT历史和相同snapshot CSV对拍通过。控制子进程正常退出0。
- 降温10.002636→9.975441℃是**软件仿真**，不是实物测量/品质实验；验证只需证明
  设备实际模拟执行及后续样本变化，不能把它写成真实冷库降温效果。
- 同一验证还执行原MQTT可信接纳/C入库、历史F02/F03、raw/质量/CSV保护。只有控制
  使用新进程入口，不冒充所有新遥测进程已整体串通，更不是恢复/两通知/第二机。
  设备ID为显式Broker ACL夹具，不是注册页面验收。
- 证据 `work/gated-b6e4b2469ab5450bae052f3c1690063f/gated-telemetry.json`，SHA256
  `a19b53403a7c2122b73ed6bace10486e54e4affc83e1edbc1783430d464edffb`。
  原记录source为ff3且dirty=true，后续保存不追改成干净提交实测；退出0/清理true，
  外发0/complete_e1=false。Compose原有重复runner stop警告保留，另回读标签无遗留。
- Ruff/format/mypy/ty通过；没有源码变化的前端/14设备/全历史回归不重跑。

原证明仍有采集与控制混合线程；新增开关只取代控制部分，子进程活着不作为订阅或
送达证据，必须经过后面的真实执行ACK和数据变化。失败也先停止/回收本轮线程，
再回收自己创建的子进程，强制kill则如实失败，不隐瞒超时。

## 剩余

同版全部进程/页面组合、安全恢复、B持久通知、SMTP+飞书授权终端实收、真正第二机
及最终M01—M05仍需完成。入口候选和局部分段通过不能关闭完整E1或代替参赛投递。
