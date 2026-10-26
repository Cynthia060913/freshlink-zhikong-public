# 01 需求与交付物覆盖表（M03 候选，供 B 汇编）

软件来源 `bfb2c927f2a1a083ac21996844d38ac4eb4abbef`（A PR82 集成候选，非已发布 main）。
本表按赛事硬要求 R01—R11 与 M01—M05 逐条对照，只写当前源码与已证结果，未实现或未验收
的明确标注。两库、三批同品种苹果、14 逻辑设备为固定范围，不删；算法非强制，不为加分扩范围。

验证层级：S=源码/单测，P=实际 PG/HTTP，M=实际 MQTT/ACK，N=合成派发，X=未完成/待实测。
一个层级只说明"做过这类验证"，不等于该条已最终签收。

## 1. 需求 → 实现 → 证明 → 缺口（R01—R11）

| 需求 | 实际模块/页面或 API | 所需证明（最低） | 当前层级 | 缺口及负责人 |
|---|---|---|---|---|
| R01 设备注册/查询/详情 | `/devices`（注册表单：设备编号/名称/类型）+ `/api/v1/devices` | 管理员注册；普通用户只读；重复不增数；非法/不存在/未登录拒绝 | S + P（G5 register_201 / duplicate_409 / saved_detail）；浏览器 X | 浏览器现场验证（A） |
| R02 在线状态监测 | `/devices` 连接状态（在线/离线/未连接·未知）与连接事件记录 | 合法心跳刷新；停发超时离线；恢复关联原离线；重复心跳不重复告警 | S + 历史分段（62bb2ef）；bfb 级浏览器/14 设备 X | bfb 级在线/离线同版复验（A） |
| R03 远程控制 | `/devices` 详情内"远程制冷控制"（准备下发/确认下发、命令有效期、查询命令与回执）+ `/api/v1/devices/{id}/commands` | 唯一命令；实际执行 ACK；重复不重复执行；控制改变后续仿真数据 | M（G1 控制 ACK、execution_count=1、10.002809→9.980946°C 软件仿真）；浏览器 X | 浏览器交互待现场；仿真≠实物制冷（A） |
| R04 故障记录与溯源 | 设备连接事件 + `/alerts` 状态迁移记录、source_event_id / related_source_event_id | 来源 ID/时间/原因/恢复可查；重启不丢 | S + P（G1 raw_preserved、recovery resolved）；完整时间线浏览器 X | 同版完整时间线 + B 追溯章节（A/B） |
| R05 实时采集与展示 | `/telemetry` "最新已存观测"（每 10 秒） | 合法 MQTT 上报进真实库；页面看到同对象/时刻/单位 | M + P（G1 四进程 receive/store/evaluate/deliver-high、raw_preserved） | 浏览器刷新与延迟实测（A/C） |
| R06 清洗与质量 | `/telemetry` 质量标记（首次保存/重复/冲突/无效）、raw_text 保留 | 原始值保留；缺测/非法/重复/冲突标记；高温不删成正常 | S + P（G1 raw_preserved、G3 PG 幂等/冲突） | C 多批 revision-only 续验与保存原文一致性待 C 正式修复（C） |
| R07 历史与导出 | `/telemetry` "按筛选新建快照"、固定快照分页、"导出本次快照 CSV"、观测原始证据 | 同筛选分页与 CSV 一致；权限相同 | P + M（G1 same_snapshot_csv、G3 authenticated_http_snapshot_csv） | 同版浏览器导出复核（A/C） |
| R08 异常告警持久化 | `/alerts`（告警列表、状态/级别筛选、查看/处置、状态迁移记录、确认告警） | 同事件幂等；确认/恢复/关闭分开；投递失败可重试 | S + P（G3 lifecycle 幂等/409；G1 one_observation_one_b_event） | 浏览器同版；B 持久派发/通知 attempt 未接（B） |
| R09 至少两种独立告警 | 通知 attempt 候选（SMTP + 飞书手机端） | 同一异常分别到邮箱与 APP；分别记录服务接受与终端实收 | X（G1 notifications_sent=0） | SMTP/飞书授权与实收（B 本人 + 收件人） |
| R10 仓库/批次/规则业务支撑 | 无专用页面；后端 B 模块 API/初始化（alerts_warehouse/batch/rule） | 两库三批持久化；规则来源/生效版本/恢复条件；历史不被新规则重写 | P（G1 one_observation_one_b_event、normal_rule threshold 20）；页面入口无 | 无页面入口需说明；B 本人确认规则（B） |
| R11 安全与复现支撑 | 登录/权限（管理员写、普通只读）、身份/主题校验、去重/重启/迁移、无秘密 | 活跃登录/写权限；无秘密；第二电脑从交付包启动 | P（G5 anonymous_401 / real_login_admin / duplicate_409；G1 source_dirty=false） | 第二机复现（A+C）；完整安全复核（三人） |

## 2. M01—M05 交付物覆盖

| ID | 交付物 | 现有工具/来源 | 当前层级 | 缺口 |
|---|---|---|---|---|
| M01 源码 | `export-source-candidate.ps1 -Revision <SHA>` → ZIP + manifest.json | S/P（G4 source 逐字节核对） | 候选底稿 | 冻结版未定；秘密/许可证人工复核 |
| M02 应用 | `build-application-candidate.ps1` + `check-application-candidate.ps1` | P（G4 构建 + 源核对；G5 默认 CMD HTTP） | 部分实测 | 镜像归档/获准获取途径；第二机启动 |
| M03 设计 | `A-SYSTEM-DESIGN.md` + `design/` 图件 + 本测试章节 | S/P | 候选底稿 | B 汇编、C 测试章节、冻结版重反射 |
| M04 PPT | `A-DEMO-COMPONENT.pptx`（PR77 五页素材） | S | 素材就绪 | B 总 PPT 成品 |
| M05 视频 | `A-CONTROL-ACTUAL-COMPONENT.webm`（控制片段）+ 本演示脚本 | S | 片段候选 | 完整主链/两通知/第二机镜头 |

## 3. 明确不存在的功能/页面/入口

- 仓库/批次页面：**不存在**。冷库/批次/规则由后端 B 模块 API 与初始化准备（R10），前端无
  对应页面；不能按设计稿写成已实现。
- `/items` 与 `/admin`：上游 FastAPI 模板的示例物品/用户管理页，**不是**冷链仓库/批次业务。
- 设备绑定入口：设备页只显示 `warehouse_id`（"未绑定"），**没有绑定按钮**；绑定由现有 API/
  初始化完成，界面不可操作。
- "再点一次验证幂等"的 ACK 按钮：**不存在**。后台幂等（G3 acknowledge idempotent）与界面
  操作分开记录，界面不提供"再点验证幂等"入口。

## 4. 边界

- 两库/三批同品种苹果/14 逻辑设备为固定范围；软件仿真不是实物果品实验。
- 算法（Isolation Forest/阈值基线）为可选加分，当前未启用，不扩范围、不填准确率。
- 本表是"覆盖/缺口"底稿，不构成任何 M01—M05 或完整 E1 签收。
