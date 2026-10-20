# A 供 M03／M04／M05 汇编的架构与证据底稿

2026-10-20。**供 B/C 审阅的底稿，非最终设计文档、ER 成图或验收签收。**
后续必须按同一冻结发布 SHA、实际运行配置、第二机结果重新核对。未通过项不能
删除标记后变成既成事实，也不能把下述不同版本/专项的通过数相加。

## 同日22时补充（优先于下方早间快照）

当前集成候选895d257已原样合入C e6bb8ef，完整后端1012通过/0跳过/92%；实际
浏览器运行4765b53在新版C下控制ACK→连续MQTT→A持久队列→C存储→历史页/固定
快照52条CSV通过，后续2e71c7e仅补证据说明。精确版本/哈希和未验边界在PR37/38，
不是正式发布或完整E1。后文早间e16/6af仅保留为历史材料来源，不当成最新头。

新增[总ER源码核对表](A-SCHEMA-SOURCE-INVENTORY-DRAFT.md)：在无网络只读容器
从精确895源码提取31张表、21条显式FK，按模板/A/B/C/候选状态分组；没有连接
用户数据库或建表。可供B现在汇编M03/M04，但最终须在正式迁移/真实PG上重取核验。
不是当前main已发布31表，不能根据同名UUID字段补画不存在的物理关系。

正式B01→A04→C仍待B安全返修/审合；A24恢复前沿是待审未实现提案，自动恢复
保持不启用。通知第二适配器/授权/真实终端实收、14设备、第二机与最终M01—M05
尚未签收。S0最低已收尾；原20日20:00完整E1已延期，27/30日出口不变。

## 1. 已确认需求与方案取舍

项目面向同品种苹果冷链仓储，约定范围为两冷库、三批次、十四逻辑仿真设备。
软件赛道允许模拟设备，不将仿真温度变化表述成实物制冷、品质概率或节能成果。
必选能力是设备注册/在线/控制/故障追溯，持续传感采集/清洗/历史/导出，以及
至少两种独立真实告警方式。智能算法是可选增强，不以模型优先挤掉业务闭环。

采用同一 FastAPI 主应用及 React/TypeScript 页面，PostgreSQL 保存业务状态，
Mosquitto 传递模拟设备消息；仿真器、MQTT 接收/控制和任务处理器属于独立运行
组件。没有设计成三个互不相通的项目，也不因多进程就宣称微服务/高可用集群。
三台成员电脑共享 Git 代码及任务记录，并不共享各自的数据库或 localhost 服务。
首选本地 Docker 交付，不把购买云服务器列为比赛最低要求；公网部署另行评估。

## 2. 架构图绘制的准确组件与边

以下是后续架构图的素材表，不是这些连接已在最终包全部运行的证明。

| 组件/边 | 职责与真实边界 | 源码入口或依据 |
|---|---|---|
| 浏览器 → FastAPI | 登录令牌；读权限与管理员写权限；根 API 前缀只加一次 | `backend/app/main.py`、`backend/app/api/main.py` |
| React 静态资源 ← 主应用 | 构建产物由主应用提供，不要求评委另启前端开发服务器 | `backend/Dockerfile`、`frontend/vite.config.ts` |
| FastAPI → PostgreSQL | A 设备/命令，B 仓储/告警，C 观测/质量/快照等各有所有权 | 对应模块 models、候选 metadata 与唯一 Alembic 链 |
| 仿真器 ↔ Broker | 独立身份/主题，心跳和采样上报，接收控制并生成执行 ACK | `simulator/`、`scripts/mqtt-runtime/` |
| A MQTT 接收 → 持久队列 → C | 先可信接收/保存，C 清洗保留 raw；数据库回执不冒充设备 ACK | `backend/app/modules/iot/telemetry_worker.py`、`telemetry_inbox_models.py` |
| A 控制 worker ↔ Broker | 从已存原命令/租约发送；执行 ACK 通过校验落库才标成功 | `backend/app/modules/iot/control_worker.py`、`control_storage.py` |
| A 设备事件 → B | 设备离线/恢复使用稳定来源 ID；B 接受与 A 检查点分别提交 | `backend/app/modules/iot/event_worker.py`、`event_dispatch.py` |
| C → B 规则/异常/恢复 | 按原异常时刻选历史规则、冻结正文；恢复必须关联已接受异常 | `backend/app/modules/telemetry/` 与 `docs/modules/C/integration-notes.md` |
| B → 邮件/第二独立终端 | 发送服务接受与终端实收分别验证；配置与收件许可私下保存 | `backend/app/modules/alerts/notifications/`、#8/#6 实际记录 |

截至本文，正式 B/C/A04 唯一迁移与完整同版 E1 仍未验收。不得把图上所有连线
都画成“已部署成功”；用候选/待验图例区分，不写未经证实的消息延迟、吞吐或 SLA。

## 3. ER 成图前的 A 实体与关系核对表

共同 main 在本轮发布核验为 `75c1e86b984b8241bab1ecd5790a89225809d306`，
A03 为其已发布迁移源码头。下表七张 A 表有该迁移定义和独立临时 PG 证据；
**不表示用户原业务库已经执行升级**。用户、模板 Item 等表另在总图列明来源。

| A 表 | 主键 | 已声明的数据库关系 | 绘图注意 |
|---|---|---|---|
| `iot_device` | `id` | 本身是被关联设备 | `warehouse_id` 是业务标识，不应伪画成已有 B 外键 |
| `iot_heartbeat_message` | `device_id,message_id` | `device_id → iot_device.id`，删除级联 | 心跳重放记录，不是每条原始遥测 |
| `iot_heartbeat_conflict` | `device_id,message_id,fingerprint` | 同上 | 冲突证据与正常接收分开，不覆盖原消息 |
| `iot_connectivity_event` | `source_event_id` | `device_id → iot_device.id`，删除级联 | 恢复关联和 warehouse 标识属业务关系，非已声明外键 |
| `iot_command` | `command_id` | `device_id → iot_device.id`，删除 RESTRICT | `actor_id` 保留历史 UUID，无级联删历史；同设备有效命令有条件唯一约束 |
| `iot_command_attempt` | `attempt_id` | `command_id → iot_command.command_id`，删除级联 | 一条命令可多次有界尝试，不能画成一对一 |
| `iot_command_receipt` | `command_id,fingerprint` | 同上 | 同命令可保留迟到/冲突等回执，不是只保留最终成功行 |

依据为 `backend/app/modules/iot/models.py`、`control_models.py` 与
`backend/app/alembic/versions/a01_device_registry.py`、`a02_heartbeat_storage.py`、
`a03_control_storage.py`。最终图必须再次从实际发布迁移/PG 元数据核对主外键，
不要根据字段名猜关系；“物理外键”和“业务引用”使用不同图例。

A04 六表（队列 guard/inbox/receipt/audit、事件 checkpoint/retry audit）当前是
PR34 的非活动冻结候选。C 已接受 B01→A04→C 后继，A04 新头
`aa497b8142574c0d2f8fc56767dcbea191069563` 已获 C 同头批准及适用 CI；
正式激活仍待 B01 安全返修审合和同版迁移验证。最终图需加入候选 guard 的 id=1
单例种子要求，不能只画表而漏掉容量/并发锁的必要初始化。

C 最新已核实候选 `6af7041a48301524749d0eec647f6e86d5be8ea4` 为十表，
恢复窗口新增 `last_received_at_utc`、`last_received_observation_id`。这是独立
candidate metadata，不是已经发布的 C 迁移。B/C 的实际实体/约束由各自作者
提供及复核，A 不在这里编造总表数或替两位签收。

## 4. 一个可讲清的设备控制链

管理员在页面选择已绑定且可控制的执行器，确认后生成一次控制意图；后端保存
稳定命令 ID、原参数、期限、操作者和状态。worker 从持久队列领取短租约，
提交后才发非 retained MQTT 命令。Broker 的 PUBACK 只证明传输接受，不能
标成设备已执行。模拟执行器按命令 ID 去重并返回 ACK；服务器校验来源设备、
topic、结构/时间和命令状态后保存 ACK，页面再读取终态和追溯记录。

这条设计处理的典型失败：HTTP 重试不生成重复意图；Broker/worker 重建后仍用
原命令；超时后收到迟到 ACK 只保留证据，不能把原超时改成成功；普通账号
不能通过直接 POST 绕过页面权限；缺真实仓库依赖不能用恒 True 假绑定掩盖。

实际浏览器专项来源是 `a16a5ce185908e0eee0ce97d4f9a96c89bbd0f94`，其后仅补
证据说明的集成头为 PR14 `6430f3b0b457a10da189fbece15dfc0dbd2ba047`。
该专项实际页面→HTTP→PG→认证 MQTT→仿真执行 ACK→页面通过，同 ID/刷新不
重执行，普通账号 POST403。温度 10.002593→9.952129℃只是该仿真场景反馈。
完整来源、报告哈希和未测边界见 PR14 的 `docs/modules/A/20-current-integration-proof.md`
及 #6 评论6284495816；不是最终发布版或 C 历史页面/两通知/完整 E1。

## 5. 给 B 的文档/PPT素材与给 A 的视频镜头清单

- 需求页：上述冷链小范围和必选业务闭环，明确纯软件仿真；不要写全行业平台。
- 架构页：组件表与物理/业务边界；候选、已发布源码、实际运行分开标注。
- 数据库页：七张 A 表关系及迁移来源；最终 B/C/A04 合链后再出总 ER 图。
- 设备章节：未知/在线/离线语义、稳定命令与 ACK、超时/重放、故障时间线。
- 测试页：只引用精确版本/输入/实际/证据；C 汇编，不跨版本累加测试数。
- 视频候选镜头：登记与重复拒绝 → 合法心跳/停发离线 → 异常可追溯与两种
  终端实收 → 管理控制/实际 ACK/后续采样 → 恢复保留历史 → 同筛选 CSV。
  此为待最终同版重录的镜头安排，不是这些镜头都已有成品。

两通知必须由接收者确认实际收到并保存脱敏素材；没有就保留“待验”，不可拿
Mock、站内提示、服务成功响应或两个邮箱替代两独立渠道。视频不剪掉失败后
冒称功能通过；课题/方案/测试写具体工程取舍，不编造纯人工开发工时/贡献。

## 6. 最终替换和签收出口

27日及之前完成必选功能/真实闭环/第二机并冻结候选；28—29日复核同版
M01—M05、尽早投递，30日及之前实际提交。B 负责 M03/M04 汇编，C 负责
数据章节与测试汇编，A 负责架构 ER/运行包/设备章节和视频；角色不因本底稿
改变。仅25—27日确有大量必选积压/本人无暇时按明确小包另行交接。

最终需替换：唯一发布 SHA、正式数据库 ER、同版场景和第二机证据、两通知
实收、实际性能/限制、成品文件/哈希/复核人及实际投递凭证。以上未齐之前，
本文、PPT结构和测试工具都不能作为“参赛材料已完成”的签收。
