# A1.1 隔离 MQTT 权限验收

此目录仅是测试夹具，**禁止作为正式 broker 配置部署**。账号密码全是公开的临时演示值；监听仅容器内 127.0.0.1，运行时网络为 none、不发布主机端口。数据库、现有 app、真实通知服务不参与。

## 复现

在项目根目录、Docker 已正常运行时，先获取官方固定镜像，再执行 Windows 检查脚本：

```powershell
docker pull eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408
powershell -NoProfile -File .\scripts\windows\check-mqtt-boundary.ps1 -Image eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408
```

Docker 不在 PATH 时，脚本可额外传 `-DockerExecutable` 指向实际 docker.exe；不要修改系统安全策略或删除数据重试。镜像按 digest 固定，首次需要联网拉取，后续测试本身无外部网络。

检查使用真正的 mosquitto_pub / mosquitto_sub：

- 合法设备凭据发布固定 JSON，观测者接收文本与夹具完全一致。
- 原始字节以 `mosquitto_sub -N` 写入容器内文件，再用 `cmp` 比对（保留原文件末尾换行）。旧PowerShell文本比较未验证行尾，本轮已改正并实际复测。
- 合法设备向别的设备遥测主题、向命令主题发布，收到真正的 MQTT5 PUBACK Not authorized (135)。本镜像客户端这种情况下仍可能退出 0；脚本断言 broker 回执，不能只判断程序退出成功。
- 匿名连接和错误密码连接被拒绝。
- 成功或失败均核对本轮随机容器名及专用 label，再清理该容器；不批量删除其他容器、镜像或卷。

2026-10-12 上述检查在 Windows PowerShell 5.1 实跑通过。曾发现脚本的 stderr 处理/清理引用兼容性和发布退出码误判，均修复后复跑；不是隐去失败只记录计划。保留的失败测试容器也已核对身份并清理。

## 不证明什么

夹具设备没有注册到正式数据库。测试没有调用 receive_telemetry 或 C 服务；没有实现生产凭据管理、TLS、心跳、持久队列、控制执行、告警或 E1。测试用 retained 消息仅解决订阅顺序，不代表决定生产遥测策略。正式跨主机通信须另行设计加密与密钥管理，不能搬用这里的公开测试密码。

配置依据：[Mosquitto 配置文档](https://mosquitto.org/man/mosquitto-conf-5.html)、[发布客户端文档](https://mosquitto.org/man/mosquitto_pub-1.html)。

接收端不追加换行的依据：[订阅客户端文档](https://mosquitto.org/man/mosquitto_sub-1.html)。加 `-SaveReceipt` 可在本机私有 `work/a-mqtt-receipt-*` 下保存实际接收文件；不会把原包写进源码目录。

另运行 `scripts/windows/check-mqtt-registry.ps1` 可将这个实际接收包送进一次性PostgreSQL注册适配检查。仓库服务为明确替身、无C入库或E1，范围详见 `docs/modules/A/03-registry-adapter.md`。
