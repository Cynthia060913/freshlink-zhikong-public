# A→C 实际 MQTT/事务/快照联调候选

2026-10-19 检查入口已转为 C be795f2；来源与实际新结果见
[当前候选兼容复测](19-current-c-compatibility.md)。下面保留2026-10-17的旧版本
证据，不以旧6d结果代替当前新组合实测。

2026-10-17。这是 E1 的一段**隔离候选验证**，不是正式应用上线或整条 E1。

## 版本与安全边界

- A 源码基于 A06 `63b1f354ccecd534d98d716e11b49496c7f6025a`，含 A/B
  真实仓库 lookup/一次绑定、A03 迁移；B 源码仍继承候选1e5e005，已知 F02
  三轮问题不被本次正常仓库接入测试掩盖，B PR10 仍待修复。
- C 固定公开提交 `6d05d75d58dc23de48872ba2985a8f129c35ae49`，只归档
  `backend/app/modules/telemetry`。在可丢弃容器 `/tmp/telemetry-proof` 内
  合成有限测试运行目录；**不改本机 A/B/C 源码、不合并 C 分支/旧历史**。
- 原应用库及宿主 Python 不使用。PG18 的 tmpfs、认证 Mosquitto、内部网络
  均属于每轮唯一 `telemetry-proof-...` 项目；无主机端口、持久卷或真实通知。
- 在任何测试 DDL 前要求 exact test-db/freshlink_test/FreshLink-test、固定
  C SHA，拒绝覆盖证据。C 候选 metadata 的 create_all **仅用于此库**，
  不是正式 Alembic。正式迁移继续按已合入的唯一 A→B→C 链，不另建 head。

## 实际调用链

管理员真实登录 HTTP→实际 B 冷库创建→A 设备一次绑定→认证传感器 MQTT
publish→只读服务订阅→`receive_registered_telemetry` 重新查注册/实际 B
warehouse→关闭 A read Session→C `ingest_observation` 的真实 PG 事务→
确认 reception 已持久后才 MQTT ACK。设备自报 received_at 原文保留，但不能
冒充可信接收时间。身份来自经过实际验证的 Broker ACL，不把 topic 当认证。

回调是**测试驱动**：不是已实现生产用耐久 inbox、断网恢复或自动重试 worker。
暂时失败在驱动中保留同一 envelope，撤去故障后原 reception_id 重试；
不能把这段内存协调说成已经通过进程重启或可靠后台调度。

## 可重复检查

在含该 A 候选的工作区、Linux Docker 引擎和已有缓存镜像可用时：

```powershell
powershell -NoProfile -File .\scripts\windows\check-telemetry-mqtt.ps1
```

要求 Git 对象中已有固定 C 公共提交；缺失时停止，先安全 fetch 新公开库 C
分支。不从旧私有库拉历史，不自动安装宿主依赖、不拉镜像。Windows脚本只用
Git/Docker，保存私有 work/<本轮ID> 归档和 JSON；finally 仅清自有容器/网络。

新增标准 Ubuntu24.04 Actions job `a-c-telemetry-mqtt` 会构建可丢弃应用镜像
并运行相同入口，仅 contents:read，不部署、外发或上传原始配置/报告。
新 job 与已有后端/覆盖率/安全/浏览器门禁并存，结论以实际同 HEAD 运行回读。

## 覆盖与不覆盖

测试断言：原文精确保存/可信接收时间；同 envelope 调用重试与网络重发分别
保持同 reception/新 reception；业务重复与改正文冲突留证；非法单位/时间
保留 raw 并 held_unknown；晚到真实高温保留；controller 越权 publish 被实际
Broker 拒绝；伪设备载荷/坏JSON/retained拒绝；C事务中途故障全回滚且不ACK，
恢复后同 envelope 原ID重试；快照两条分页/total/内部CSV成员和顺序完全一致，
后续提交只进入新快照。预期最终6 observation、8 reception、8次持久后ACK。

没有验证：生产耐久接收队列/重试预算/进程重启、正式 C 迁移和应用 HTTP/CSV
下载权限、B规则评估/候选outbox/告警恢复/两渠道实收、真实浏览器操作、十四
设备和第二电脑。C 无规则风险保持 unknown，不把真实高温直接写成已告警。
原告警、设备控制和最终材料仍在各自任务验收，S0同版签收不由本测试代替。
