# A04：服务器控制 MQTT 发送与执行回执（待审候选）

2026-10-15；继承 PR13 的 A03 `30b6729fa2ba4bd864eb4c56f44eaa40880ed5f2`，
安全合入共同 main `f7fe62a95eceee75bebba3e135fc67d6c7728643` 后独立开发。
尚未部署应用数据库、挂生产 worker、接真实 B/C，不能据此签收 R03/E1。

## 行为和边界

- `ControlWorker` 使用 A03 已保存的原始命令包；事务内先领取持久租约，
  提交后才发 QoS1、非 retained 的 MQTT 命令。命令 UUID、参数和期限不重造。
- 显式执行器 UUID allow-list 最多14个，只订阅各自 exact `acks`；无全局
  通配符。MQTT身份来自事先验证的 Broker 账号/ACL，不相信正文自报身份。
  `acl_confirmed` 只是操作人声明，不是远端 ACL 审计。
- 默认 TLS，证书和主机名验证保持启用。明文只允许显式隔离测试的本地地址。
  密码由调用方在内存中提供，设置和无秘密说明不保存密码。
- **Broker PUBACK 只记录 sent/传输接受，不记录设备已执行。** 设备 ACK
  经 A03 的设备/topic/结构/时间/状态校验并提交后，才向 Broker 确认收件。
  错误或 retained ACK 不改命令成功；存储异常停止且不确认未保存的 ACK。
- MQTT队列/在途上限14；单 owner 处理网络回调和短事务，不开无界后台队列。
  连接/连接池/SQL等待各有5秒限制，但不是整个扫描周期的硬实时保证。
  PUBACK默认等待5秒；握手/订阅5秒未就绪、断网或发送失败时退避重建客户端，
  丢弃 Paho 易失的旧发送队列。数据库租约/原包和设备 command_id 去重负责恢复。
- Broker quota/未指定错误等暂时失败重试；鉴权、topic、payload明确拒绝为永久
  失败。整体尝试预算、原期限和不可立即反向控制规则沿用 A03。过期扫描不依赖
  MQTT连接就绪；timeout后真实晚ACK只保留late证据，不把超时改成功。
- 启动时必须注入 `warehouse_lookup_factory(Session) -> WarehouseExists`，
  没有恒True默认。集成入口需接B真实仓库服务、同Session和可信设备绑定；
  目前不提供伪仓库的生产CLI，也不修改应用Compose自动启动该候选。
  HTTP创建依赖仍默认503直到实际B接线，不修改根路由/认证/共享迁移。

## 实际验证

1. 缓存Docker / Python3.14.8、独立PostgreSQL完整后端 **340 passed / 96%**；
   新增44个适配器测试，worker语句覆盖100%（不等于完整安全/并发证明）。
   同范围Ruff、全app mypy/Ty通过，保留原90%全后端覆盖门禁。
2. 独立认证Mosquitto + PostgreSQL + FastAPI TestClient + 持续仿真器：
   - 真实心跳使设备在线；管理员POST→命令队列→MQTT→仿真制冷→原执行ACK
     →PostgreSQL/API终态；后续实际MQTT采样温度下降。
   - 同HTTP意图及真实Broker重放原命令，第一条命令执行次数仍1，回执不重复插入。
   - controller账号伪造ACK发布被Broker拒绝，不产生假执行。
   - 设备停止时命令虽获Broker接受仍不算执行。真实停止/重建服务器worker，
     重启设备后从持久队列投递原包，第二条合法命令执行并回执。
   - 再停止设备，短期命令到期为timeout；设备重启后收到过期原包，拒绝执行并
     生成真实expired ACK，服务保留late证据，状态仍timeout，总执行次数仍2。
3. 首轮测试有一项错误使用Paho `qos`属性；改为实际`QoS`后完整重跑通过，
   未跳过用例或降低门禁。保留原失败记录，不把首次失败写成一路成功。

本地证据：`work/a01-tests-20261015170849-42484`、
`work/control-mqtt-20261015171105-37592/mqtt-control.json`。
后者包含两条成功控制、超时命令、实际ACK时间和温度变化，不含业务凭据。
所有账号、设备、数据库和仓库lookup均为标记清楚的合成测试；**温度下降不是
实物冷库效果、品质或节能率**。临时容器/内部网络已清理，原业务库和宿主Python未改。

## 安全复测和集成待办

Windows项目目录中运行 `powershell -NoProfile -File scripts/windows/check-control-mqtt.ps1`；
完整后端用 `check-a01-acceptance.ps1`。只用已缓存镜像、唯一临时项目和tmpfs，
无主机端口、不连外部通知，失败保留work证据，清理仅本次临时容器/网络。
单元测试位于现有 `tests/`，随后端CI执行；真实Broker检查是本机独立验收，
当前没有把它冒充远端CI实跑。

下一步：同HEAD远端检查/交叉审查→真实B仓库依赖和唯一迁移链→正式运行入口
与控制页面/浏览器→A设备事件/B告警/C数据及两通知真实E1→14设备/第二机/材料。
正式TLS/ACL配置、Broker重启/网络故障注入、生产长时运行、B/C持久实链、
UI、真实通知、跨机复现均未在本检查中完成，继续留在#5/#6/#7/#12追踪。

实现参考：[Paho官方客户端文档](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)
（manual_ack、QoS确认、重连后易失队列重发语义）。
