# 服务端心跳接收与离线扫描（本地候选）

2026-10-14，继承已验证的 A02 持久化提交
`1d02960ba46796123a65484f07f5f0d626f06006`。
本轮新增 `heartbeat_worker.py`；没有 C/B 调用、公开写心跳 HTTP 接口或
应用数据库升级。共享部署/迁移、独立审核、S0/E1 尚未签收。

## 接收安全和可靠性

- 必须由维护者先验证 Broker 认证/ACL：每个设备账号只能写自己的完整
  心跳主题，服务账号在心跳主题上只读。订阅者收到的 MQTT 包不自带可信
  发布者身份；topic 只是经过这些 ACL 后的身份映射，不是独立认证。
- 配置限1—14个不重复的已注册 UUID；不订阅全局通配主题、不读取包内
  UUID 来建立身份。配置 `acl_confirmed` 必须明确为 true，但此标记不会
  代替对远端 ACL 的验证。凭据撤销/ACL更新与配置刷新仍需维护者协调。
- 默认 TLS8883，验证证书/主机名、TLS至少1.2；明文只供明确隔离的本地
  测试。配置不存密码，运行时读取持有人环境变量。
- 单一线程循环处理网络和事务，避免无界队列或跨连接旧 mid 误确认。
  连接池获取、PG连接和SQL/行锁均设置有限等待；DB故障显式停止，不续
  在线、不发成功ACK，也不输出DSN、异常原文、配置或攻击原包。
- QoS1手动确认只发生在持久事务完成或明确拒绝之后，遵循
  [Paho官方manual_ack说明](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)。
  QoS0心跳是新鲜、尽力发送，不补造离线期间的数据或宣称零丢失。
- 订阅保留 retainAsPublished，正在运行时的 retained 心跳也不能续在线。
  超长/错误身份/越权消息不改变状态，日志仅记录固定原因码。
- 每5秒扫描配置范围；网络短断按1—30秒间隔重连，独立扫描仍运行。
  DB故障则停止，由进程监督/人工处理后重启；不能继续显示服务健康。

## 实际检查

| 项目 | 本轮结果 |
|---|---|
| 缓存镜像中的完整后端测试 | 255 passed，95%总覆盖率，1项框架弃用警告 |
| 新worker安全/停止/ACK顺序单测 | 26项；worker覆盖率91% |
| Ruff、mypy | passed；没有系统Python安装/卸载 |
| 真Mosquitto + PostgreSQL + 登录API | unknown→online→offline→online；恢复关联原离线 |
| 重复与认证拒绝 | 重复不续在线；retained/错误身份不续；设备跨主题和服务账号写心跳均被Broker拒绝 |

真实链路使用 Docker 内部网络、唯一临时库、无宿主端口、公开合成测试
账号。仅将离线阈值缩为3秒便于受控验证，生产逻辑默认90秒不改。
证据 `evidence/a-heartbeat-mqtt-20261014.json`，详细报告保存在
`work/hb-mqtt-20261014041829-52796/mqtt-heartbeat.json` 和
`work/a01-tests-20261014041849-45456/pytest.xml`。测试容器和合成数据已清理，
应用数据库卷未动，证据保留。

复测入口 `scripts/windows/check-heartbeat-mqtt.ps1`；必要缓存镜像、临时库
限制和未测项由脚本检查/报告。无须用户逐条输入容器命令。

## 配置与尚未完成

格式示例 `simulator/heartbeat-worker.example.json` 故意保留
`acl_confirmed=false`，防止拷贝即跑；需实际核查 ACL、注册UUID与证书后
在本人私有目录保存有效配置。密码只来自
`FRESHLINK_HEARTBEAT_MQTT_PASSWORD`，不能提交环境文件或把授权码发聊天。
容器内以已锁定依赖运行 `python -m app.modules.iot.heartbeat_worker --config`
和私有配置路径；首次正式应用启动需审查后接 Compose，不安装本机依赖。

生产 TLS/ACL自动配置、凭据撤销流程、进程健康监督、完整14设备容量、
数据库长期去重保留策略、浏览器实测、B事件投递/真实通知、C数据持久化、
控制API与ACK仍未完成。本次实链不是E1，不宣布主程序上线或参赛合格。
