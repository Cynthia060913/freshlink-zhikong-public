# A 技术附件（设备、共享入口与 A→C 接线）

> M03 候选章节，供 B 直接汇编设计文档。软件来源固定 main0e
> `0e508d147517f81dfe2b9245c45c0c0e30076592`（PR82/88 已合并），不是后来 main，
> 也不代表完整 E1、两通知实收、第二机或 M01—M05 已签收。本文只陈述真实已验证范围，
> 未完成项不写成通过；C/B 证据明确归属，不代三方签收。

## 一、范围与非物理试验声明

系统面向两座苹果冷库、三批同品种苹果，软件模拟七个测区，每区一个温度采集身份与一个
制冷执行身份，共十四个逻辑设备。主要操作包括：注册并绑定设备、查看在线状态、持续查看
原始/清洗数据、查询同筛选历史与 CSV、下发制冷命令、核对设备执行 ACK 与后续温度、处置
异常并保留追溯。通知须 SMTP 与飞书手机端两个独立渠道授权实收，服务接受不等于终端实收。

本项目的温度与制冷数据来自软件模拟，不是实物果品实验；阈值异常不宣称为真实腐败概率。
算法是可选加分项，本附件不涉及。项目不要求采购硬件或付费服务器，交付用软件模拟与隔离
容器验证。所有涉及“已通过”的表述只对应下方列出的候选与分段证据，不与不同版本测试数
相加，也不把隔离回归写成业务现场验收。

## 二、部署与模块分层

- 展示层：React/TypeScript/Vite，公共认证与导航，设备、控制、B 告警、C 数据页面。
- HTTP 层：FastAPI。`backend/app/main.py` 仅在总入口加一次 `settings.API_V1_STR`，
  `backend/app/api/main.py` 聚合子路由，不在 alerts 聚合器上再加 `/api/v1`；设备绑定、
  控制和遥测子路由复用公共登录权限。
- 感知/执行层：`simulator_runtime.py` 与 `simulator_fleet.py` 七测区监督器；每设备独立
  身份与 MQTT ACL，逐测区 SQLite 只保存模拟器本地执行状态，不是服务器业务数据库。
- A 服务层：设备注册、心跳与在线离线、控制事务及回执、MQTT 接纳与持久收件队列、有界
  租约与重试、A→B 设备事件投递。
- B 服务层：冷库、批次、版本化规则、异常事件、状态变化与通知 attempt。
- C 服务层：观测 raw 与质量、历史、固定快照分页与 CSV、评价、outbox 与恢复进度。
- 存储层：PostgreSQL 保存设备/控制/A 队列与 B/C 业务；Mosquitto 做设备消息路由。

进程启动不等于全部 worker 已运行；容器、进程与数据库需分别验证，不能只靠网页可打开
验收。隔离验证在 Windows 上 Docker 容器内进行：Python 3.14.8、PostgreSQL 18、固定摘要
Mosquitto，随机专属内部网络与 tmpfs 数据库；不修改宿主 Python，不发布 PG/MQTT 宿主端口。

## 三、数据、控制与恢复路径

遥测链：设备温度消息 → A 校验身份/绑定并持久接纳 → C 同事务保存 raw/质量/观测 → 历史
页面与同快照 CSV → 调用方选择 B 生效历史规则 → C 门禁候选与持久 outbox → B 摄取接口
接受、来源摘要回读与追溯 → 已提交事件通知派发 → SMTP 与飞书终端分别实收（末两步仍未
签收）。同筛选 CSV 与页面/历史集合按完整 ID 对拍，不是只比条数或 Mock 响应。

控制链：登录用户下发 → 持久命令/尝试 → MQTT 执行设备 → 设备事务保存执行结果并返回原
ACK → 服务端回执 → 后续温度采样反映制冷效果。仅创建命令、MQTT publish 成功或页面显示
“发送”都不能写成已执行；设备 ACK 之后有新的温度数据才称执行闭环。同版十四设备为两库
三批、14 在线、7 采集流、7 执行器 ACK 后降温、14 离线恢复追溯、重启不重发；其中多批
高温候选经鉴权 ASGI HTTP 写入 B 并回读批次/规则版本匹配，ASGI 不是 TCP。控制实测温度
下降（如 10.003187 → 9.851915 °C）是短时软件仿真，只证明 ACK 后数据变化，不是实物制冷
或已降到恢复阈值。

恢复路径（main0e 受保护恢复后台发送候选）：`RecoveryDeliveryWorker` 轮转发现每页最多
14 条到期恢复，在已核对设备/绑定/epoch 范围内，由 C 在同一 Connection/事务中锁设备、
重验完整前沿、保存证据并 CAS 领取；外层提交失败绝不传出临时 claim，提交成功且连接已
释放后才调用 C 的已领取发送口，该口重新确认当前租约、查 B 同来源、必要时 POST、核对原
异常已 resolved 再作 fenced 回执。独立 TCP 进程实际领取并回读原异常 resolved，重启一次
不重复投递、原正文/hash 不变、attempt=1；迟到更正由独立进程完成 B 双 ID 追溯。此角色
显式选择 `deliver-recovery` 并提供 `--confirm-recovery-contract` 才启动，不发送 SMTP/飞书。

## 四、事务、权限与追溯

A 提供的 `begin_evidence_write` 要求 C 使用同一调用方 Connection 在写前取得同设备门禁、
写后保存冻结戳；没有覆盖所有写入口就不能启用自动恢复。网络派发不放在持数据库锁的事务
内。A 保留原异常行锁、锁后重读、单调状态时间、事务失败回滚与 HTTP 503+Retry-After。
设备采用逐设备账户/ACL，生产默认 TLS；隔离证明的明文 Broker 只在随机内部网络。

登录权限、摄取权限、设备身份与通知收件授权分别验证，不互相替代。异常恢复要求完整可信
窗口、关联原异常、重放/并发安全与晚到证据处理；不完整或停机缺口不能判正常，恢复关闭
异常也不删除原始超温与历史暴露。已有 attempt 的冻结恢复遇到新前沿时 C 仍保守 held，不
重写证据；本轮未证明所有延迟/新接收交错都能自动收敛，held 状态不写成恢复成功。

## 五、数据库与 ER 来源

共同源码唯一迁移链为 A03→B01→A04→A05→C01，C01 是唯一 head，不是平行建表。在专属空库
实际升级并通过 model check 后，只读反射得到 33 张表；本附件不做第二次反射，直接引用
`docs/submission/design/` 已核对的图件与清单，33 表反射比较显示 changed_table_definitions
为空（无 schema 变化）。三张核心 ER 图只展示 19 个核心实体、17 条已逐项与实际数据库外键
核对的边，完整字段见数据字典 `docs/submission/design/DATA-DICTIONARY.md`，边见
`docs/submission/design/er-edges.json`。

关键关系：心跳/冲突/在线事件和 `iot_command` 通过外键引用 `iot_device`，命令设备外键删除
策略为 RESTRICT；`iot_command_attempt`/`iot_command_receipt` 引用 `iot_command`，删除策略
为 CASCADE；`alerts_warehouse → alerts_batch → alerts_batch_rule_snapshot`；
`alerts_event → alerts_status_transition`/`alerts_notification_attempt`；C 质量、接收/评价、
outbox 与快照成员按边清单中的外键关联到观测；接收重用审计行关联原接收记录。`iot_device
.warehouse_id`、C 观测的设备/冷库 UUID、outbox 与 B 来源等属业务逻辑引用而非数据库外键，
不按字段同名擅自补线。这些表由三人各自模块共建，33 表并非全为 A 开发。图见
`docs/submission/design/tikz_architecture.png`、`tikz_er_a.png`、`tikz_er_b.png`、
`tikz_er_c.png`，均为实际编译渲染；最终冻结版若有模式变更须重新反射与出图，不能只替换
SHA 标签。

## 六、已实测与仍未验收

结论按验证层级区分，不同 SHA 分别说明，不加总：

| 验证项 | 实际结果 | 证据与边界 |
|---|---|---|
| 后端回归 | 本地 1446 passed / 0 skipped / 91%；适用云端 1444 / 2 分别列 | `work/a01-tests-20261026222407-79444/pytest.xml`；XML 汇总 tests=1755 含 subTests，不当新增用例 |
| 受保护恢复后台 TCP | 独立进程 CAS 领取并回读原异常 resolved，重启不重发、attempt=1 | `work/gated-a9aed80a7c84448d89612ba511419db9/runtime-composition.json`，source_revision 1c85ae2 |
| 同版十四设备分段 | 两库三批、14 在线、7 采集流、7 执行器 ACK 后降温、14 离线恢复、重启不重发、同快照 CSV 对拍 | `work/gated-fleet-7cc435cc846e4b25935d311fa624ca8d/gated-fleet.json` |
| 应用镜像归档 | 540475904 字节，sha256 f7a5e3d5…bc0b806；默认 CMD 真实 HTTP 健康/登录/注册/重复拒绝/详情通过 | `work/image-archive-20261026231221-351a4765a356/image-archive.json` |
| 33 表反射 | 无 schema 变化 | `work/notify-subset-c97d15c346fa49f2b2d845a49ec2425c/design-schema-comparison.json` |

仍未验收（不写成通过）：SMTP 与飞书手机端两渠道授权实收、同一冻结版本完整 E1、最终同版
第二机复现、C 自动恢复与多批高温业务收尾、M01—M05 汇编与实际参赛投递。受控规则切换
（测试专用 1°C 阈值）是工程验证，不是果品温度真实升高；`notifications_sent=0`、
`complete_e1=false`。文档与代码编写使用了辅助工具，按赛事要求如实披露，不把辅助过程
写成纯人工开发经历。
