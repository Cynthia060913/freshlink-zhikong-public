# 鲜链智控软件设备运行器

入口为 `backend/app/modules/iot/simulator_runtime.py`，复用软件设备模型。它是真正的持续 MQTT 客户端，不再通过文件桥转交命令。**当前只完成独立设备侧运行和隔离Broker验收，尚未接入应用的设备授权、在线状态、C入库或B告警。** 不把固定测试UUID称作已注册设备。

## 配置及运行

`runtime.example.json` 是无秘密格式示例，UUID来自公开测试夹具，**必须换成已登记并由有效冷库绑定的真实项目UUID及Broker账号**。应用尚未开放完整绑定/凭据配置入口时，不绕过它宣称生产接入。将本人配置与状态保存在被Git忽略的work目录；不要把私有配置、状态文件或真实日志放到本目录。

正常连接默认TLS8883，保留服务器证书和主机名检查；私有CA可通过ca_file设置。配置没有明文密码字段，持有人在本人运行环境设置 `FRESHLINK_SENSOR_MQTT_PASSWORD` 和 `FRESHLINK_ACTUATOR_MQTT_PASSWORD`。测点、执行器分别使用最小主题权限；不要共享管理员账号、将账号授权码发群或关证书检查。

已有锁定Python3.14环境中，可从backend目录启动：

```powershell
uv run --package app python -m app.modules.iot.simulator_runtime --config ..\work\simulator-config.json
```

配置中的state_file相对路径按启动目录解析；请使用本人固定绝对路径，确保重启使用同一状态文件。Ctrl+C正常停止，容器可用SIGTERM；不重置设备文件。`--max-seconds 60`可做有时限运行，不传时持续到停止/明确错误。运行器不启动FastAPI或自动购买/部署服务器。

当前默认30秒采样、30秒心跳、10秒重传。隔离测试缩至0.5秒，仅测试节奏，不代表正式告警时延或性能指标。明文仅允许明确isolated_test_plaintext且host为localhost/127.0.0.1/专用测试broker的受控场景；**此配置不能直接充当生产服务**。本仓库的compose.simulator-tests.yml只有Docker内部网络，无宿主端口、无应用DB、公开测试凭据。

## 关键行为

- 连续上报保存原始message_id/字节，未得到C业务持久确认就不清待发；Broker PUBACK不是C入库。重启原包可重发，未来C按同ID幂等。
- 定时发两台逻辑设备各自的schema1.0心跳，新UUID、UTC发送时刻、非retained；心跳不跨停机持久排队，避免陈旧心跳冒充在线。
- 执行器在真实命令主题订阅，网络线程只放有界队列；设备状态/原ACK事务提交后才确认接收命令。非法命令只留有界hash/字节数/原因审计，不留攻击原文或密码。
- 手动确认、VERSION2回调、后台网络循环依据 [Eclipse Paho官方接口](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)。订阅保留retainAsPublished以识别正在运行时的retained命令；仅QoS1命令进入执行路径。
- 原执行ACK持久待发，设备进程在ACK发出前崩溃可恢复；同命令重发只重传原ACK，不重复执行。ACK的Broker传输标记不表示主程序已持久接受。重复恰好撞上旧ACK在途时，旧传输不能清新一次重传请求。
- 丢连接时客户端重新连接；原采样/ACK由应用持久状态补发，不能依赖MQTT库的内存队列作为跨进程可靠性。
- 超过60秒停机间隔显式保存restart_gap，温度/电量冻结、制冷关闭，需要新命令；不补造缺口观测。旧ACK只作为历史执行证据，不能证明重启后当前开关仍开着。时钟倒退拒绝。
- 队列满、状态文件不可写或长期无确认等明确错误停止设备，保留待发/审计；不丢包后仍称正常工作。待发1000、命令10000、审计1000等为工程限制，不是性能承诺。

完整源代码/测试/实测边界见 [A运行阶段说明](../docs/modules/A/05-continuous-runtime.md)。下一步仍是服务端心跳持久化/去重/在线离线恢复、正式命令API及BC业务接线，不是更换客户端或重建工程。
