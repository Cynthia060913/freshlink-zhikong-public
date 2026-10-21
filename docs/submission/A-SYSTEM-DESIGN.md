# A 提供的设计文档章节：设备、共享入口与运行边界

2026-10-22 编写，供 B 汇编 M03、PPT 和 A 录屏脚本使用。**候选章节，不是最终
设计文档签收**。源码核对基于 `2eac312a33e313263ee5b67e86e997794c713c09`；
PR44 独立发布候选为 `a35a1e3f31db4dc89d5b1dc5b04f022761ef1e65`。
后续冻结时必须把本章、截图、测试报告、源码和运行包替换为同一实际发布版。

## 1. 需求与边界

系统面向两座苹果冷库、三批同品种苹果，软件模拟七个测区，每区一个温度采集身份
和一个制冷执行身份，共十四个逻辑设备。不要求采购硬件或云服务器；模拟数据不是
实际果品品质实验，阈值异常不宣称为真实腐败概率。算法不是必选功能。

主要操作为注册并绑定设备、查看在线状态、持续查看原始/清洗数据、查询同筛选历史
和 CSV、人工下发制冷命令、核对设备执行 ACK 和后续温度、处置异常并保留追溯。
通知必须有 SMTP 与飞书手机端两个独立授权实收；服务接受不等于手机或邮箱实收。

## 2. 当前源码的分层架构

- 展示层：React/TypeScript/Vite，公共认证及导航，设备、控制、B 告警和 C 数据页面。
- HTTP 层：FastAPI。`backend/app/main.py` 仅在总入口加一次
  `settings.API_V1_STR`；`backend/app/api/main.py` 聚合子路由，**不能在 alerts
  聚合器上再加一次 `/api/v1`**。设备绑定、控制和遥测子路由复用公共登录权限。
- 感知/执行层：`simulator_runtime.py` 等现有设备运行器和
  `simulator_fleet.py` 七测区监督器；独立设备身份、MQTT ACL、逐测区 SQLite
  执行状态。SQLite 是模拟器本地执行状态，不是服务器业务数据库。
- A 服务层：注册/心跳/在线离线、控制事务及回执；MQTT 接纳与持久收件队列；
  有界租约和重试；A→B 设备事件投递。不能把进程启动等同于全部 worker 已运行。
- B 服务层：实际冷库、批次、版本化规则、异常事件、状态变化和通知 attempt。
  新通知适配及派发增量仍待缺陷修复与集成，不作为已发布能力描述。
- C 服务层：观测/raw 与质量、历史、固定快照分页/CSV、评价和 outbox/恢复进度。
  已有源码和临时 PG 验证；正式迁移、全部写入口门禁及恢复发送一致性仍待收尾。
- 存储层：PostgreSQL 保存服务器设备、控制、A 队列和 B/C 业务。Mosquitto 提供
  设备消息路由；容器、进程及数据库分别需要配置和验证，不能只靠网页可打开验收。

## 3. 一条实际业务链应怎样验收

设备温度消息输入 → A 校验身份/绑定与持久接纳 → C 同事务保存 raw/质量/观测 →
历史页面及同快照 CSV → B 按生效规则生成异常与追溯 → 已提交事件通知派发 →
SMTP 和飞书终端分别实收。

人工控制是另一条关联链：登录用户下发 → 持久命令/尝试 → MQTT 执行设备 →
设备事务保存执行结果并返回原 ACK → 服务端回执 → 后续温度采样反映制冷效果。
仅创建命令、MQTT publish 成功或页面显示“发送”均不能写成已执行。

异常恢复还要求完整可信窗口、关联原异常、重放/并发安全和晚到证据处理。
不完整/停机缺口不能判正常；恢复关闭异常也不删除原始超温与历史暴露。

## 4. ER 绘图前的真实关系底稿

下表只列实际模型中的外键，不把业务 UUID 相同自动画成数据库外键；最终图需
用冻结版本实际迁移再核对，不能把候选 C 表当作已发布表。

| 父实体 | 子实体 | 实际关系/源码 |
|---|---|---|
| iot_device | iot_heartbeat_message、iot_heartbeat_conflict、iot_connectivity_event | 设备外键，`modules/iot/models.py` |
| iot_device | iot_command | device_id 外键，RESTRICT，`modules/iot/control_models.py` |
| iot_command | iot_command_attempt、iot_command_receipt | command_id 外键，CASCADE，同上 |
| alerts_warehouse | alerts_batch | warehouse_id 外键，`modules/alerts/models/batch.py` |
| alerts_batch | alerts_batch_rule_snapshot | batch_id 外键，`modules/alerts/models/rule.py` |
| alerts_batch、alerts_warehouse | alerts_event | 可选 batch_id/warehouse_id 外键，`modules/alerts/models/event.py` |
| alerts_event | alerts_status_transition、alerts_notification_attempt | alert_event_id 外键，同上 |
| C observation | C quality/reception/evaluation/outbox/snapshot_member | `modules/telemetry/storage_candidate.py`、`outbox_candidate.py`、`snapshot_candidate.py` 的显式 ForeignKey |
| C reception | C reception_reuse | reception_id 外键，`storage_candidate.py` |
| C outbox、snapshot | 各自 conflict、snapshot_member | `outbox_candidate.py`、`snapshot_candidate.py` |

源码相对目录均以 `backend/app/` 为起点。A 持久队列使用 reception_id/fingerprint，
A→B 投递使用 source_event_id/target_event_id 做业务关联，不能擅自画额外 FK。

## 5. 迁移、事务和安全说明

PR44 的唯一候选是 A03→B01→A04，A04 六张 A 表已通过真实升级/旧数据保留；
PR46 的 A05 两张前沿表是后继候选，C 后继仍须协调唯一父节点。实际 main 状态
须临提交重新读取，不能把本段当作永远最新或直接升级原数据库的指令。

`begin_evidence_write` 要求 C 用同一调用方 Connection 在写前取得同设备门禁，
写后保存冻结 stamp；没有所有写入口覆盖就不能启用自动恢复。A36 显式空库起点
函数不会从启动过程调用，也不删除或重置历史。网络派发不放在持数据库锁的事务内。

设备采用逐设备账户/ACL，真实部署默认 TLS；隔离证明的明文 Broker 只在随机
内部网络。真实配置/邮件地址/授权码/机器人 hook/原数据库及私有日志不进源码包。
登录权限、摄取权限、设备身份和通知收件授权分别验证，不互相替代。

## 6. 可用于报告的证据与不能写的结论

- A36 组合全后端 1162 testcase、零失败/错误/跳过、93%：A36 列实际 XML/哈希。
  这是隔离回归，不是 1162 次业务现场验收。
- A35 已完成十四身份/七测区真实隔离 MQTT、执行 ACK 重放、降温及跨身份拒绝。
  不含实际三批业务、C 正式发布、两通知实收或第二机，不能写成完整场景通过。
- A32 真实浏览器/HTTP/PG/MQTT 分段证据含 C 临时测试 DDL；不能写成正式 C 迁移。
- M01/M02 已有 a35 源码候选和镜像默认命令 HTTP 验证；最终版本变化后须重打包。
- 全部 E1、两渠道终端实收、第二机、最终 PPT/视频及实际投递仍未签收。

B 可现在汇编需求/方案/结构正文；结果章节仅填真实已验证范围。A 录屏和最终图
在同版 E1 稳定后补；C 提供真实测试/数据章节。不得编造纯人工开发经历或成员贡献。
