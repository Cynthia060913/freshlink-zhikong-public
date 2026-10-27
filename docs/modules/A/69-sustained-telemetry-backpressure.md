# A69：持续采集积压与测试 Broker 重启修复候选

2026-10-27，北京时间晚间。基线 main 为
`0e508d147517f81dfe2b9245c45c0c0e30076592`；本文不是 main 发布、完整 E1、
第二机或参赛材料签收。后端业务源码固定于
`62190fea8dba44bc3a37fae411ce17494c8f538c`，验证器收尾修正于
`10632233e616f0b28c9441797900edbd83904358`，二者 app/tests 字节相同。

## 实际问题与最小修改

同一专属通知试验继续跑设备控制时，设备执行 ACK 和后续降温样本已经出现，
随后 receiver 以 `telemetry_admission_unavailable` 停止。只读检查为 admitted
1068、settled 70、998 queued，gap 为 `untracked_legacy_or_refusal`。这些数据
保留在原专属数据库；本修复不清空队列、不重置 gap/epoch/前沿，不把旧流恢复
为健康流。该试验也不因此变成完整 E1。

设备日志没有 C 存储确认通道，原发布器会随 pending 增多扩大重放；store 原来
每处理一条就休眠 0.5 秒。现在仅改 A 的两个业务文件：

- simulator_runtime：每设备每个单调时钟一秒窗口至多调用 publish 四次，失败
  也消耗预算；新样本优先，旧样本轮转且保留原重发间隔和 in-flight 检查。
- telemetry_worker：每轮最多处理 14 个已就绪领取，每条仍单独保留原事务、
  租约、同 Connection C 写入和回执验证。无就绪项、非 delivered 或异常立即
  退避；逐条检查停止与截止时间，不引入无界忙循环。

不修改 C/B、协议、正式迁移、容量限制、权限或通知行为。MQTT PUBACK 仍不是
C 存储确认，设备原始日志不因 PUBACK 删除。

隔离 MQTT fixture 另有独立启动缺陷：当前固定 Mosquitto 的密码工具拒绝
`-c` 覆盖已存在路径。修复在随机临时目录中的新文件生成全部账号，再原子替换
密码文件；不删除 broker 持久消息目录。第一次临时文件方案同样失败，保留在
提交历史；有效修复为 `e258d2853358d007fd8e16bbfaa2fe06be2b6437`。

## 实际验证与范围

所有结果均来自本机专属容器和数据库，不用宿主 Python，不连接原项目数据库。

| 验证 | 实际结果 | 本地证据（work 为私有，不进提交） |
|---|---|---|
| 完整后端 | 1455 passed、0 skipped、91%；app 类型检查 116 文件无问题 | a01-tests-20261027211156-16104/pytest.xml；runtime-backpressure-types-retry.log |
| 持续真实 MQTT→接纳→C | 180.96 秒；331 个唯一原始样本与设备日志 ID 全等；最大采样 lag=4，最终追平、gap 空 | sustained-4828c3b39535487fbaceacf7166745c3/sustained-telemetry.json |
| 控制／恢复／更正 | 真实执行 ACK、后续 C 数据、TCP B 恢复、更正关联、sender 重启零重发通过 | gated-e314a4f3b5f745c7876e9f529b859b84/runtime-composition.json |
| 两库三批 14 设备 | 现有存储／控制分段通过 | gated-fleet-8ba6b393a7314d2a8193fd220c1d5610/gated-fleet.json |
| Broker 两次重启 | 认证和保留消息均保留 | mqtt-restart-d929e340ae584f7e8e476ec53feef075/mqtt-restart.json |
| 固定归档镜像 | 10632233e616 的 Git 归档构建与镜像源码审计通过 | source-candidates/20261027212743-6d0e822d2bb745bda270f786fadd5d08/application-build.json |

持续结果 SHA256：
`5f4901f2308c61ce12447a9409b4edc1cdcd34523ff460ebf926683283d295fd`。
控制／恢复结果 SHA256：
`39a7ec4681f4b3e6861201dde99f35bdca5e7b5c292f9c02ecca7684b684cd40`。
14 设备结果 SHA256：
`1eb1f07c104a3e4e5c4bff77f1b23b799974cd2e2d225a0198ee15e08512c6b2`。

第一次持续验证的 180 秒运行未见积压爆发，但最后检查失败：验证器先停止设备
连接，再要求尚未发送的日志尾部已经到 C。失败保存在 sustained-9878e4b44eeb45d99e70612438b8d4b1。
现验证器在设备所属线程暂停新增采样，让真实 MQTT 发完尾部后才断开。该步骤
只属于验证器；**产品 stop() 仍是保存未发尾部供下次重启，不承诺自动排空**。
第二次验证为全新专属库，未覆盖第一次结果；两个项目均实际清理核验。

## 复现与未通过项

在项目根目录，用已有可信缓存应用镜像（需含本项目锁定依赖）：

```powershell
pwsh -NoProfile -File scripts/windows/check-sustained-telemetry.ps1 -AppImage <已有镜像>
pwsh -NoProfile -File scripts/windows/test-mqtt-fixture-restart.ps1
```

脚本以随机 Compose 项目、内网、空库唯一正式迁移链运行，不开宿主端口，不
读真实通知配置。失败也保存有限诊断并清理本轮资源。

180 秒是单设备高频持续验证；14 设备结果是现有业务分段，**不是 14 设备连续
180 秒满速压力测试，也不是无限运行保证**。原有设备日志 1000、流接纳 10000
等有限容量与 fail-closed 仍存在；本补丁没有存储确认协议或自动 epoch 轮换。
达到这些边界应保留数据并报告停止，不能偷偷清空或重建前沿。

真实飞书手机端已由接收者在本会话确认，SMTP 首次尝试失败阶段不明、未获
补发许可；本候选测试新增真实通知为零。不同版本、不同专属库的分段结果不能
拼成同版完整 E1。C 机器上的恢复失败仍须本人在有效候选上复测，本文不代签。
