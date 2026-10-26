# 02 可汇编测试章节（M03 候选，供 B/C 汇编）

软件来源 `bfb2c927f2a1a083ac21996844d38ac4eb4abbef`（A PR82 集成候选，非已发布 main）。
逐条写源码 SHA、环境、输入/步骤、预期、实际、证据、未测项。先列本任务书五组已证结果，
再列失败/未完成。层级区分源码/Mock/PG/真实 HTTP/进程/浏览器/终端实收/第二机/完整 E1；
不同版本测试数不相加，不填最终通过结论。

## 1. 环境

Windows 上 Docker 隔离容器：Python 3.14.8、PostgreSQL 18、固定摘要 Mosquitto；随机专属
internal 网络、tmpfs 数据库；不修改宿主 Python。证据根目录为 A 本机私有 `work/`，公开包只
引用脱敏说明与文件相对路径，不把绝对私有路径写成拿到包后仍可访问的依赖。

## 2. 五组已证结果（均按实际读取标注边界）

### G1 运行合成：MQTT/PG/TCP、更正后台、控制 ACK 及后续数据
- 证据：`gated-40c012abee254ec4b5ace0dbf4cb8bac/runtime-composition.json`（已实际读取）。
- 输入/步骤：干净 bfb 源，真实 PG18 + C01 迁移/check + 内部 MQTT + 真实 TCP；四遥测进程
  receive/store/evaluate/deliver-high；重放改温 MQTT 触发后台更正进程；显式调用恢复 HTTP；下发控制。
- 预期：原异常 resolved 回读、raw 保留、同快照 CSV、设备执行 ACK 且只执行一次。
- 实际：source_dirty=false；actual_recovery_http_and_resolved_readback=true；
  actual_late_mqtt_correction_background_tcp=true；raw_preserved=true；same_snapshot_csv=true；
  控制 command_id `4cfae65a-…`、execution_count=1、温度 10.002809→9.980946°C（软件仿真）。
- 边界：notifications_sent=0、complete_e1=false；not_tested=无人值守恢复调度/通知/浏览器/14 设备/第二机/发布批准。

### G2 本次运行退出与清理
- 证据：同目录 `runtime-child-cleanup.json`、`cleanup.json`（已实际读取）。
- 实际：test_exit=0、owned_cleanup_ok=true；子进程退出码 receive/store/evaluate/deliver-high/
  deliver-correction/watch-restarted/check-recovery/watch-recovery=0，check-stale=2、uninitialized=1、
  api=-15（SIGTERM）；forced=[]。
- 边界：仅本次测试退出/清理，不等于其他机器或正式运行。

### G3 专项组合（后台更正 + B 更正契约 + 生命周期 + PG 存储 + worker）
- 证据：`notify-subset-814b3af0f2344c018face5124026d9f5/current-c-b83.xml`（已实际读取）。
- 实际读取该 XML：tests=72、errors=0、failures=0、skipped=0，time=5.822s，
  timestamp=2026-10-26T05:49:37Z，hostname=10487f5d0262。
- 覆盖：correction delivery/wire、PG storage（含 authenticated_http_snapshot_csv）、
  B 更正契约、alerts lifecycle（含 acknowledge 幂等/409、normal_user 只读）、worker 退避。
- 边界：**这是 9fedf0c 组合的专项子集，不是 bfb 全套回归**；任务书/A62 标注"69 过/0 跳过"，
  本文件实际读到 72 项，二者不一致，最终以该 XML 实际内容与 A 核对为准。

### G4 应用镜像构建与源核对（165 后端文件 / 39 前端产物）
- 证据：`source-candidates/20261026162745-1b4fc0b5ff0d43fab7d08eb7245ab63f/application-build.json`（已实际读取）。
- 实际：status=candidate_image_built_source_verified_not_application_acceptance；source=bfb；
  source_zip_sha256=4d2876e7…；image=freshlink-application-candidate:bfb2c927f2a1-b57fa4f783cd；
  image_id=sha256:3a09d10c…；backend_files_verified=165、frontend_files_present=39。
- 边界：不验证启动/正式迁移/E1/第二机/通知/完整镜像安全审计；构建仍需网络解析 build-system。

### G5 镜像默认 CMD 真实 HTTP 冒烟
- 证据：`image-http-20261026163228-b15f1c9793ef/application-http.json`、`image-cleanup.json`（已实际读取）。
- 实际：status=archive_image_default_cmd_http_smoke_passed_not_E1；checks 含 compiled_html_and_javascript、
  default_cmd_http_health、anonymous_401、real_login_admin、register_201_unknown、
  duplicate_409_total_one、saved_detail；cleanup proof_exit=0、cleanup_exit=0。
- 边界：无源码挂载、默认 CMD；不是浏览器交互、不是第二机、不是完整 E1。

## 3. 失败/未完成（不写成通过）

| 项 | 状态与证据 | 负责人 |
|---|---|---|
| 无人值守恢复发送 | 未完成；当前恢复 HTTP 由显式验证驱动，后台更正实跑 ≠ 无人值守恢复完成 | A+B |
| SMTP + 飞书两渠道终端实收 | notifications_sent=0，未实收；GitHub 通知邮件不算项目 SMTP 实收 | B 本人 + 收件人 |
| 第二机同版复现 | 未签收；同机新容器/新 Engine ≠ 第二台电脑 | A+C |
| 完整 E1 | complete_e1=false | A+B+C |
| C 多批 revision-only 续验 / 保存原文一致性 | 待 C 正式修复；A 私有提案 89 项通过只是提案验证，#5 评论 6409087579 | C |
| WH1 多批告警 | 历史分段仍 hold；不是多批告警完成 | A/B/C |

## 4. 汇编要求

- 不同 SHA、不同层级的测试数与文件计数分开记账，不相加成"完整回归"。
- 本候选章节不含最终通过结论；最终 M03 须补齐其他模块、完整 E1、失败场景、第二机，并统一
  冻结版逐项签收。
