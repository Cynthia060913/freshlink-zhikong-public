# A 演示组件：每页讲稿与来源对照

本表对应 `A-FINAL-COMPONENT.pptx`（由 `build-a-final.mjs` 生成，6 页，16:9，微软雅黑，
文字与表格可编辑）。固定软件来源 main0e `0e508d147517f81dfe2b9245c45c0c0e30076592`
（PR82/88 已合并），不是后来 main，也不代表完整 E1 或 M01—M05 已签收。

每页讲稿写入下方“讲稿要点”列，供 B 直接采用为 M04 素材组件；来源列为可追溯的
仓库相对路径或 A 私有运行证据白名单文件。渲染 PNG 与整组概览在本地
`work/claude-final-materials/png/`、`deck-montage.webp`，不随仓库提交。

| 页 | 标题 | 讲稿要点 | 来源与证据 | 本页未写成通过 |
|---|---|---|---|---|
| 1 | 冷库业务与角色范围 | 两座苹果冷库、三批同品种苹果、七测区十四逻辑设备；数据是软件模拟，不是实物果品实验，阈值异常不代表真实腐败概率。A 负责设备注册/在线/MQTT 接纳/控制回执与数据链，B 负责批次规则/告警处置/两通知与追溯，C 负责遥测清洗/历史快照/评价。 | `docs/submission/A-SYSTEM-DESIGN.md` §1；软件来源 main0e | 两通知实收、完整 E1、第二机、M01—M05、实际投递均未签收 |
| 2 | 共享系统架构与模块归属 | 展示层 React/Vite，HTTP 层 FastAPI 总入口只挂一次 API_V1_STR；A/B/C 各自服务层，PostgreSQL + Mosquitto 存储与消息路由。图中组件存在不等于后台调度全部启用。 | `docs/submission/design/tikz_architecture.png`（TikZ 源 `tikz_architecture.tex`，已编译渲染并逐张看图）；`A-SYSTEM-DESIGN.md` §2 | B 独立持久派发、C 自动恢复、两通知实收未签收 |
| 3 | 采集、控制与恢复的实际证据 | 采集是真实 MQTT→A 接纳→C 同事务保存；观测是 raw/清洗/历史与同筛选 CSV（浏览器 23 行一致）；控制是下发 set_cooling→MQTT→设备 ACK→后续采样降温（10.003187→9.851915°C，软件仿真非实物制冷）；恢复是后台受保护 TCP 发送（见第 4 页）。 | `docs/submission/A-TEST-REPORT-CANDIDATE.md`；`A64-INTEGRATION-CHECKPOINT.md`；截图 `admin-real-ack.png`（来源 `work/control-browser-20261027001102-63760/browser.json`、`server.json`，command_id 3aad6c63，execution_count=1） | 高温三批为 ASGI HTTP 到 B（非 TCP）；notifications_sent=0、complete_e1=false |
| 4 | 受保护的恢复后台发送与同版十四设备 | main0e 受保护恢复：同一事务内 CAS 领取，提交并释放连接后才锁外发送与 fenced 回执，重启不重发。同版十四设备：两库三批、14 在线、7 采集流、7 执行器 ACK 后降温、14 离线恢复追溯、重启不重发；多批高温候选经鉴权 ASGI HTTP 到 B（非 TCP）。 | `docs/modules/A/65-guarded-recovery-delivery.md`；`work/gated-fleet-7cc435cc846e4b25935d311fa624ca8d/gated-fleet.json`；`work/gated-a9aed80a7c84448d89612ba511419db9/runtime-composition.json`（source_revision=1c85ae2） | 注册 ID 是显式 ACL 夹具；ASGI 不是 TCP；仍有 held 组合未证明自动收敛 |
| 5 | 同版应用镜像与 33 表 ER | 应用镜像归档 540475904 字节、sha256 f7a5e3d5…bc0b806；默认 CMD 真实 HTTP 健康/登录/注册/重复拒绝/详情通过。33 表只读反射无 schema 变化，19 核心实体、17 条真实外键边；唯一迁移链 A03→B01→A04→A05→C01 空库升级通过。 | `docs/submission/design/README.md`；`tikz_er_a.png`；`work/image-archive-20261026231221-351a4765a356/image-archive.json`；`work/notify-subset-c97d15c346fa49f2b2d845a49ec2425c/design-schema-comparison.json` | 镜像导入另一台电脑、运行依赖可用性、最终冻结版未验证 |
| 6 | 诚实剩余验收 | 已证：设备注册/在线、控制 ACK 后降温、raw/清洗/历史/同筛选 CSV 对拍、唯一迁移链空库升级、33 表反射、受保护恢复后台 TCP、同版十四设备分段、后端 1446 用例/0 跳过/91%。未证：SMTP＋飞书两渠道实收、完整 E1、最终第二机、C 自动恢复与多批高温业务、M01—M05 与实际投递。 | `docs/submission/README.md`（发布阻断检查）；后端回归 `work/a01-tests-20261026222407-79444/pytest.xml`（本地 1446/0/91%，适用云端 1444/2 分别列不加总） | JUnit XML tests=1755 含 subTests，不当新增独立用例 |

## A 复核更正

第4页恢复顺序为“同一事务内CAS领取→提交并释放连接→锁外发送与回执”，
不是先提交/释放连接后才领取。第6页待办指已证分段的同版联合验收，
不将已经通过的受保护恢复TCP和多批高温ASGI分段误写成尚无实现。

## 测试口径与未测项（写报告时保持一致）

- 后端回归：本地 1446 passed / 0 skipped / 91%（终端/用例口径）；适用云端 1444 passed /
  2 skipped。二者分别列，不加总。JUnit XML 汇总 `tests=1755` 含 subTests，不当新增独立用例。
- 高温三批候选的 B 传输是鉴权 ASGI HTTP，不是 TCP；真实 TCP 恢复证据在受保护恢复后台
  发送单独记录，不要混称。
- 受控规则切换（1°C 测试专用阈值）是工程验证，不是果品温度真实升高，也不是生产温控阈值。
- 控制实测温度下降是短时软件仿真，只证明 ACK 后数据变化，不是实物制冷或已降到恢复阈值。
- `notifications_sent=0`、`complete_e1=false`：两渠道终端实收、完整 E1、最终第二机、
  M01—M05 与实际参赛投递均未签收。

## 视觉与结构检查边界

- 已运行 finalizer：package integrity、layout geometry（bullet/heading fit、16:9 尺寸）、
  字体（Microsoft YaHei）、可再导入均通过，0 finding / 0 warning；1 张原生可编辑表格（第 4 页）、
  3 张真实嵌入图、每页讲稿存在。
- Claude 初交时：逐页 PNG 已导出，但其视图工具不能显示，故当时未完成逐页视觉复核；
  当时的 finalizer 结构/几何检查不能代替人工查看。
- A 后续实测状态：已逐页查看六页，修订的2/4/6页重新渲染并复看；finalizer 检查亦通过。
  当前可供 B 汇编，具体来源、工具与边界见 [A复核结果](A-REVIEW-RESULT.md)。
