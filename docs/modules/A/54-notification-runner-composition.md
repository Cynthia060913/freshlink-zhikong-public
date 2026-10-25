# A54：持久通知运行器接线候选

2026-10-25；基于 A53 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`。
本候选不自动合入 main、不启用真实发送、不升级原数据库。

## 限定来源与事务边界

- B 运行器来源为 `a54e477cf1a086a7d22ab2189e592ff9b9819acd`。
- `runner.py` 与 `test_dispatch_runner.py` 两文件采用 C PR72 精确
  `b4027b0cba293d3fb05262524397a3d7fd87ad35` 的版本，包含按实际配置渠道扫描的
  饥饿修复；没有整支合并 B/C，也不代称 B 已接受 PR72。
- 保留 A53 已包含的原异常行锁、锁后重读、单调状态时间、事务失败回滚和
  HTTP 503/Retry-After 防护。B 当前分支与这些文件有分歧，不能整支覆盖。
- A/C 摄取完成并经 HTTP 写入 B 已提交 `alerts_alert_event` 后，由独立 B runner
  扫描同一数据库。通知不进入 MQTT 回调、C 写事务、原异常锁或 HTTP 请求事务。
  扫描依靠已有事件/attempt 表，不新增迁移。
- 单部署使用一个 runner 与独立 PG advisory lease；持久 attempt 记录提供重启后
  去重、预算及退避。网络已接受但 attempt 未保存等不确定结果仍不能承诺 exactly-once。

## 隔离验证入口

共享运行入口为 `python -m app.modules.iot.notification_service --config <私密JSON>`。
默认只预览一个周期，配置字段为 recipients、max_events_per_cycle（默认1）、
max_attempts_per_channel（默认1）、poll_seconds（默认60）。配置不能启用发送；
缺 SMTP 收件人时预览/发送均在连接数据库前拒绝，错误不打印邮箱/配置值。
这是对 B 公共 `run_dispatch_worker(..., recipients=...)` 的 A 启动适配，
不改 B 原模块：B 当前默认 main 没传 recipients，不能作为本应用 SMTP 启动说明。
`--send` 必须由已有具体人类许可的操作员显式选择；不能以开关本身当作许可。
`--continuous` 另需显式选择，默认不常驻；不在本轮执行任何真实发送命令。

```powershell
pwsh -NoProfile -File scripts/windows/check-gated-telemetry.ps1 -NotificationRunner -AppImage <已有兼容依赖镜像>
```

入口使用随机项目名、内部网络、tmpfs PostgreSQL、只读源码挂载与明确空的 SMTP/
飞书配置。不读取宿主私有配置；只清理本轮创建的项目。与其他验证模式互斥。

`check-notification-composition.py` 复用原四个 CLI/TCP 实跑，再启动四个独立进程：
dry-run 零发送零 attempt → 合成 SMTP 成功/APP 暂时失败 → 新进程只补 APP →
再启动无重复派发。三个实际 PG attempt 都明确标注合成收件端。
这不是 SMTP/飞书服务接受、更不是终端实收，也不是完整 E1 或第二电脑验收。

## 当前实际证据与尚缺

2026-10-25 15:23（北京时间）精确代码提交
`1b7a9d91e6a8a426eb4995e7f9114bff1a399ba7`，工作区干净：

- 完整隔离后端 **1341 个实际 testcase，零失败/错误/跳过，覆盖率92%**，不是 XML
  顶层累计计数。包括新增 A 启动适配9项、B/C runner19项、A并发13项。
  证据 `work/a01-tests-20261025152326-45864/pytest.xml`，SHA256
  `560c8081ef6f1ede487a7eda8d307e904cde8441c54f25dc405e2ed6ca0fbcad`。
- 唯一 Alembic 链空库 upgrade/check 通过。真实 MQTT→四个 CLI→C PG→TCP B
  告警→独立 B runner 合成渠道链通过；A 默认启动模块实际 dry-run，四个新的
  runner 进程验证0/2/3/3条持久 attempt、只补缺失 APP、不重复发送。
  `work/gated-ee06fdf2492f4b3d9b5205f3eb58c7dc/notification-composition.json`，SHA256
  `259a82ba46f188fd5c757aa88036cbb402503031d7e39b0a3c28afca5b728f43`。
- 两个专属项目清理后容器/网络均为空；运行子进程无强制 kill，四个角色正常退出0。
  Uvicorn 完成优雅关闭后按框架行为重抛 owner SIGTERM（-15），不是零退出；
  未初始化范围探针预期失败1。没有连接原数据库、改宿主 Python或外发通知。
- 启动器 mypy、三份新 Python 文件 Ruff 检查通过；有限公开文本扫描无检出，
  不等于历史/图片/评论或完整安全审计。

该版本仍包含 A53 已验证的 C45 业务源码；C 后续 freshness/HTTP 增量不在这次
组合内，不能把它写成最新 C 已接线。后续仅文档记录变化不重跑相同全部回归；
新增业务源码需再核对相关专项。

仍需：B 复核采用 C 的修复、C 对精确 A 候选审查；发送者和接收者私密配置、
具体发送许可、两渠道终端实收；C 恢复/修正契约及实际调度、完整同版 E1；
第二电脑最终同版复现与 M01—M05 成品。不能用本分段关闭这些缺口。
