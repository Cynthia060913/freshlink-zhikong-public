# A 提供的设计文档章节：设备、共享入口与运行边界

2026-10-24 更新，供 B 汇编 M03、PPT 和 A 录屏脚本使用。**A候选章节，不代签B/C或
最终设计文档**。业务源码固定 `71e5a8820ee966db1b250afaf0a72143a4bdc62b`（PR59依赖58），
共同main为 `a1d185eea504f4d389a65c9431bf0e62d4827cf6`，二者不可混称已发布。
新增[实际架构、三张核心ER和完整数据字典](design/README.md)：专属空库实际C01迁移/
model check后只读反射33表，19核心实体/17边逐项核真实FK，图已实际编译/渲染/逐张看图。
后续冻结若代码或模式变化须重新核对，不只替换SHA标签；当前不是M03全文签收。

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
  C01已在共同源码唯一迁移链注册并经空库PG升级验证；全部写入口门禁、恢复发送一致性
  和多批原子评价仍待收尾，不等于原数据库已经升级或后台已自动运行。
- 存储层：PostgreSQL 保存服务器设备、控制、A 队列和 B/C 业务。Mosquitto 提供
  设备消息路由；容器、进程及数据库分别需要配置和验证，不能只靠网页可打开验收。

## 3. 一条实际业务链应怎样验收

设备温度消息输入 → A 校验身份/绑定与持久接纳 → C 同事务保存 raw/质量/观测 →
历史页面及同快照 CSV → A调用方选择B生效历史规则 → C门禁候选与持久outbox →
B实际摄取接口接受、来源摘要回读及追溯 → 已提交事件通知派发 →
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

2026-10-24共同main源码的唯一链为A03→B01→A04→A05→C01，C01是唯一head，
不是独立平行建表。专属空库实际升级/model check通过，33表结构取自该迁移结果；
未升级任何原数据库。实际main仍须临交付重新读取，不把本段当永久最新或部署指令。

`begin_evidence_write` 要求 C 用同一调用方 Connection 在写前取得同设备门禁，
写后保存冻结 stamp；没有所有写入口覆盖就不能启用自动恢复。A36 显式空库起点
函数不会从启动过程调用，也不删除或重置历史。网络派发不放在持数据库锁的事务内。

设备采用逐设备账户/ACL，真实部署默认 TLS；隔离证明的明文 Broker 只在随机
内部网络。真实配置/邮件地址/授权码/机器人 hook/原数据库及私有日志不进源码包。
登录权限、摄取权限、设备身份和通知收件授权分别验证，不互相替代。

## 6. 可用于报告的证据与不能写的结论

- A45精确4792ac3核心完整后端1216 testcase、零失败/错误/跳过、92%；10项PG针对性。
  这是隔离回归，不是1216次业务现场验收；本轮设计材料未重复无变化全套。
- A46精确62bb2ef：两库三批14在线、7持续流/执行ACK后降温；停发离线与同状态重启
  恢复、关联原离线事件、C历史保留、命令不重复执行和同快照CSV完整ID对拍。
  精确71e5归档镜像不挂后端源码也完成该分段；WH1多批仍hold，不能写为多批告警完成。
- A45浏览器已改用实际C01与门禁存储，不再C临时测试DDL；后续两次成功，首轮间歇
  worker失败根因仍未知，不能删掉失败或称根治。合成B恢复不是C自动恢复/通知实收。
- M01/M02已有精确71e5源码和540MB镜像候选，默认命令真实HTTP、新目录克隆/tar重载
  通过，仍是A同机；第二机、完整离线套件与最终冻结版仍未签收。实际哈希见A46/#12。
- 全部 E1、两渠道终端实收、第二机、最终 PPT/视频及实际投递仍未签收。

B 可现在汇编需求/方案/结构正文；结果章节仅填真实已验证范围。A 录屏和最终图
在同版 E1 稳定后补；C 提供真实测试/数据章节。不得编造纯人工开发经历或成员贡献。
