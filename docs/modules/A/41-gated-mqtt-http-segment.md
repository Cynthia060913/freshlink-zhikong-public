# A41：实际 MQTT→同事务 C→JWT/HTTP/CSV 分段

2026-10-22，继承 A40，沿用当前 PR54，不再建立平行集成候选。
C 业务源码固定 `5603552718d64c36bf369d162b515dea9ce24150`，变更后必须
先重新审核本证明的假设，不能静默用旧源码结果替新组合。

## 实际执行与明确限制

`scripts/windows/check-gated-telemetry.ps1` 仅使用已缓存镜像，创建随机专属
internal 网络、tmpfs PG 与带认证/逐身份 ACL 的 Broker，无宿主端口/真实配置。
按本分支实际 Alembic 链升级到 A05 并执行 model check；随后**显式执行 C01
非活动提案，仅在本次空测试库**，不把它登记成共同已发布迁移，不升级原数据库。
用实际登录用户和 B 根 HTTP 创建冷库、A 根 HTTP 绑定；固定传感器 ID 是为匹配
测试 Broker ACL 直接注册的夹具，不冒称这里验证了设备注册页面/API。

运行当前 `TelemetryReceiver` 与 `TelemetryDeliveryWorker`，不是在 MQTT
回调中手写 C 入库。显式调用 fresh-origin 初始化器，在确证空证据库时建立 epoch；
注入同一设备/epoch 集合的 `GatedTelemetrySink`。没有自动恢复或通知 worker。

本次断言：

- MQTT subscriber ACK 前，另一个真实 PG 连接已能看到 A 原文；C 尚无观测。
- receiver 退出/重启仍读取同一 inbox reception ID/原文。
- 对 C evaluation INSERT 注入实际 SQLAlchemy 故障后，C 观测与 A evidence
  revision 一起回滚；A 返回 `retry_scheduled`，保留原始接纳并持久重试。
- 恢复正常后真正提交 C，再保存 A 终态；连续完整前沿可读。
- 原消息再次 MQTT 发布只增加接收审计，不新增业务观测。
- 缺湿度观测仍保留原文/质量，设置 hold，不能输出可用恢复前沿。
- 真实根路由匿名返回401；登录 JWT 可查询两个业务观测，CSV 与同 snapshot
  页面全部 observation ID 完全相同，详情原文与 MQTT 原始字节一致。

首轮验证器将 A 的调度结果误当 C 回执，断言 `retryable_failure` 失败；实际正确
返回为 `retry_scheduled`。已检查服务源码、保留失败目录并修正断言，没有修改业务
状态或放宽失败标准。预检成功目录 `work/gated-f50c73e59a0445b880c7191ef0d471ea`
为 dirty 开发快照，不冒称精确提交的最终证据；最终以随后清洁提交复测记录为准。

最终清洁提交 **87b5edbf6375a4483c56d0d57c07241bc0431050** 实际复测成功：
`work/gated-97b50f324b5d4d2cbc5da0bb327c106d/gated-telemetry.json`，
`source_dirty=false`，SHA256
`398460dfe495abc07650f1340427c6739e1cedc4a5e208e9a17570834ac441a0`。
3条实际MQTT接收审计、2条业务观测、原消息重复幂等、失败回滚重试/重启、
完整样本前沿可读/缺测后hold及JWT原文/同快照CSV均通过。cleanup记录退出0；
Compose日志有一次重复停止已移除runner的提示，随后独立回读精确随机前缀的
容器/网络均为空，确认没有残留；未清理其他资源。

进一步增加“任何提案DDL/夹具写入前拒绝已有业务数据”的只读守卫，并在真实
PG 中插入一条事务内负例、确证拒绝再回滚。最终守卫版清洁提交
**1b49a4fa4fe61cd0380a204e8b4684b563eead50** 又实际复测成功，
`work/gated-6edc0e35650a4b4c9d91c8d5a5ecfe55/gated-telemetry.json`，SHA256
`14d4083e7e46a1b41808fc7cae0a784bf5f5e774dcebd1943482b5e542e4e519`；
保留全部前述断言，`nonempty_database_refused_before_ddl=true`、清理退出0。

## 可复跑与 CI

先在已核验的本分支构建标准测试镜像，再执行
`pwsh -NoProfile -File scripts/windows/check-gated-telemetry.ps1`；也可显式传
`-AppImage` 选择已缓存兼容镜像。缺镜像会停止，不自动拉取/付费/改宿主 Python。
证据为私有 `work/gated-<随机ID>/gated-telemetry.json`、`runner.log` 和
`cleanup.json`。失败也清理本轮资源并保留日志；已有原库不在清理范围。
共享 telemetry 工作流增加此段，仍使用标准 ubuntu-24.04、原有权限/超时，
不上传私有日志、不增加收费 runner，不改变必选门禁。

**未验证**：C01正式发布/全部C写门禁覆盖、B规则评估/恢复发送线性化、两独立
通知终端实收、浏览器、两库三批14设备完整业务、第二机与最终材料投递。
不能把本段或继承 CI 记为完整 E1。

## 本轮协作与时间

PR44已实际合入 main `a03d14d026c786f993446bc27c4396ac8548cb0a`，新 main
自身后端/Compose/浏览器/Zizmor适用检查已通过。PR45转main时GitHub撤销原
APPROVED（变DISMISSED），A已停住请C重新对同一f52头确认；PR46不越过45。
用户已分别授权45/46，但授权不是独立review，不能绕过保护。

用户本轮最新文字再次明确：27日前必选功能/第二机完成并冻结，28—29同版材料
复核尽早投递，最晚30日前实际发送；不默认30日深夜仍可用。此前“27日实际
投递”是带日期旧要求，仍尽量提前，但按本轮最新安排；20日完整E1延期不抹去。
