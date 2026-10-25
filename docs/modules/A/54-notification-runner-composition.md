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

本轮两文件接入后，真实临时 PG 专项 **60 passed**：运行器19、派发28、A并发13；
唯一迁移链 upgrade/check 通过，清理后本项目容器/网络均为空。该专项未覆盖前端。
进程组合证据以实际运行生成的 `notification-composition.json` 与 cleanup 为准，
本说明不预先声明该入口已通过。

仍需：B 复核采用 C 的修复、C 对精确 A 候选审查；发送者和接收者私密配置、
具体发送许可、两渠道终端实收；C 恢复/修正契约及实际调度、完整同版 E1；
第二电脑最终同版复现与 M01—M05 成品。不能用本分段关闭这些缺口。
