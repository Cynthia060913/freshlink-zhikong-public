# A60：新采样后的恢复前沿续验与实际 B 回读

2026-10-26候选，基于PR79文档修复头346ac5d，保留C
`0f346e9ec5be7ac3aef74fb114c1e87f98d9fa99`四个新提交及B PR80
`b07a5e3e91cc674b3b06469a35f7c130839f1982`的独立4文件增量与作者。
不是已发布main，不夹带进PR79精确许可。C生产子树和C01提案逐字对齐0f346e9，
不新增迁移、默认后台任务或真实通知。

## A同事务接点

原recovery_observation_service保留prepare/check模式，增加互斥
`--refresh-recovery-id <uuid>`，最多14个明确互异UUID。只读作用域/发现连接结束后，
按stream→当前Device/Warehouse→outbox锁序，在同Connection调用C
`refresh_pending_recovery_frontier_on_connection_candidate`。

仅refrozen提交；held/already_current回滚。成功不增加attempt、不更改原F03
source/body/digest；存储或提交失败不返回临时成功，失效驱动连接不回池。
这不是发送许可，也不启动HTTP或自动恢复。

## 已实跑及可复现入口

八组定向实际PG测试86项通过、无跳过，包括C wire 7项、B80 13项和A新增6项：
正常新接纳后held→refrozen→ready、已leased/新高温继续held、写后存储故障和
提交故障完整回滚及连接复用、实际CLI、重复输入拒绝。原始工作树验证目录
`work/notify-subset-d270de488a6b4c0392d36ef7ef8deba5/`，清理0/无资源残留。

同一候选工作树还通过实际PG18/唯一C01升级、认证MQTT、四独立常驻遥测进程、
三条正常采样和prepare/check，随后又接纳新正常样本使旧冻结held；实际refresh
CLI提交后ready，现有C durable delivery在锁外经实际TCP B POST恢复并GET原异常
resolved。B2事件/1条转换，正文与摘要不变，NotificationAttempt0。四遥测进程在
HTTP完成时均仍存活，未通过先停receiver制造静止前沿。

上述是由显式CLI驱动的受控续验，不是无人值守恢复/更正调度。第一轮为未提交
工作树，精确提交版的最终hash/回归/证据在PR及#6按实际结果补充，不冒充冻结版。

复用原工具，无全局安装：

```powershell
pwsh -File scripts/windows/check-gated-telemetry.ps1 `
  -AppImage <existing-reviewed-image> -RecoveryContinuation
```

工具只使用本轮随机专属PG/MQTT内网与临时数据，保留严格C源码/迁移检查，
不拉新镜像、不发布PG/MQTT端口、不操作原数据库；check-stale子进程预期退出2，
其余业务CLI退出0，API由本人SIGTERM优雅关闭。结果complete_e1=false。

## 仍需收口

B80仍有原异常主体约束/列表关联两处返修，C wire准备后还需独立更正租约、
wire摘要验证、重试/完成端口；不能直接改held为pending或覆盖内部body/hash。
只允许attempt0未领取恢复重新核验，先前已尝试且前沿变化的重试保持保守held，
不得偷偷重冻已领取/已送达记录。长间隔/新高温后的新窗口语义仍须明确。

两真实渠道授权实收、无人值守调用、完整同版E1、最终第二机和材料仍未验收；
不以86项测试、此HTTP分段或CI替代。真实发送/原库升级/部署仍需相应具体批准。
