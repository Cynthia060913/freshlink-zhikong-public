# A37：显式控制 worker 入口与实际 B 绑定控制链

2026-10-22，基于 PR44 精确 `a35a1e3f31db4dc89d5b1dc5b04f022761ef1e65`
的独立小包，不包含45—50候选，不修改44审核头。没有依赖安装、自动迁移、部署、
自动恢复或真实邮件/手机推送。

## 交付目的与启动边界

原 ControlWorker 已有持久命令/ACK逻辑，但没有交付用显式 CLI。本包新增
`app.modules.iot.control_runtime`；网页启动不自动拉起它，导入模块也不建表。
只在未来同版应用镜像已重建、部署获准、设备/冷库及 Broker ACL 确认后显式启动：

```text
python -B -m app.modules.iot.control_runtime --config <容器内私有配置文件>
```

以上是受控容器内命令，不要求运行宿主Python/uv。可加 `--max-seconds 60` 有限运行；
默认持续至SIGINT/SIGTERM或失败。示例 `simulator/control-worker.example.json`
默认TLS8883，`acl_confirmed:false`，不能直接运行；本人必须替换真实已注册执行器
UUID/账号，核实账户只能发指定命令主题、读对应ACK，不能伪造设备ACK。
标记为true只是操作者确认，不会自动审计远端ACL。

密码只读运行环境 `FRESHLINK_CONTROL_MQTT_PASSWORD`，没有密码CLI参数/JSON字段。
配置读取限64KiB，拒绝符号链接/坏文件；Pydantic/文件/运行异常只输出固定原因，
不回显误填密码或数据库连接串。有限时长必须有限正数，NaN/inf在建worker前拒绝。

启动只读核对PostgreSQL、五张实际控制/设备/冷库表、1—14个明确且不重复的执行器，
以及真实B同Session查询的冷库绑定；传感器、未知设备、未绑定或不存在冷库拒绝。
事务READ ONLY及5秒statement_timeout仅作用于本次预检；不修改在线状态/绑定/命令。
预检不是完整迁移版本证书，也不抵消运行时对每条命令的重新核对。
失败释放本worker独立连接池。实际运行可能执行已排队命令，不能把启动worker当
无副作用的只读检查；本轮只在专属临时库和内部Broker运行。

## 实际运行与证据

- 新增15项测试：配置限额/固定错误/缺文件/格式、非有限时长先拒绝、真实桥依赖、
  预检失败清理、信号与运行失败清理、实际PG四种绑定/设备类型及预检无状态变化。
  与原ControlWorker测试合跑59/59，test_exit=0/cleanup_exit=0：
  `work/a-write-gate-20261022005524-14360/gate.xml`。
- 全应用mypy94文件/ty/Ruff通过。首轮SQLAlchemy select与SQLModel Session.exec
  类型不匹配，改为其原生select后通过，没有绕过类型门禁。
- 实际空库迁移/模型核对和完整后端 **1118 testcase，0失败/错误/跳过、93%**：
  `work/a01-tests-20261022005716-17196/pytest.xml`；SHA256
  `3b520afb3b99aac91f4692f8d7fea895eb0e1a531c72c67ac2d126607d523667`。
  本组合是a35加本入口，不是包含A05/十四设备/新C UTC guard的全组合，不能与1162相加。
  测试时入口业务/单测源码与本次保存一致；之后仅补独立验证器子进程和输出文案。
- 旧check-control-mqtt real模式仍替代建Warehouse表，会与真实B01重复；已删除，
  改为实际唯一B01后继/表存在守卫。actual B HTTP创建冷库→A HTTP绑定→真实桥
  →新runtime factory预检→MQTT执行/原ACK/重复只执行一次、超时迟到ACK、worker重启
  原payload保留、控制后温度下降、服务账号伪造ACK发布被拒，均在同一受控运行通过。
- 最终 `work/control-mqtt-20261022005923-71240/mqtt-control.json` SHA256
  `d115d86134c003c273b055a37b899480a25f63fe9a708365ba4181d059eef575`。
  `warehouse_mode=real`、`explicit_runtime_factory=true`；独立真实CLI子进程一秒
  有限启动/退出0，配置和密码均为合成夹具。子进程检查本身不另外证明broker就绪
  或执行新命令，命令执行已由上面的实际factory链单独证明。
- 两次MQTT项目及完整/小范围PG容器网络均清理0；合成SQLite/证据保留私有work。
  原应用库及宿主Python未操作。控制专项没有浏览器/C持久化/两通知/E1验收。

## 尚未完成

需要本包精确头独立审核与适用CI、另外的具体合并许可；44许可不覆盖本包。
旧a35导出镜像不含新模块，不能直接运行新命令；最终镜像须从冻结源码重建。
公共Compose的服务编排、正式C迁移/全写Connection、B通知修复与授权实收、
同版E1/十四设备完整业务/第二机/最终材料仍有缺口，不能因控制入口完成而签收。
