# A65：受保护的恢复后台发送候选

共同 main 已按 PR82 的精确许可合入，合并提交为
`296d4cb1a844b8eb9cd93d8506a142581921bda4`，包含批准头
`2a3e2cf2b4ad83d226626acb4bcd358b27373ab5`。本页描述其后独立候选，
不沿用 PR82 许可合并，也不表示完整 E1/真实通知/第二机已验收。

## 原作者与接线

正常合入 C `121550c468520adbbe7f35a9e286304e915d4bec`，保留 C 的原子
`claim_fresh_recovery_on_connection_candidate` 和提交后
`deliver_claimed_candidate`，C 生产子树/C01与该精确头一致。没有替 C 改锁或迁移。

A `RecoveryDeliveryWorker` 轮转发现每页最多14条到期恢复，在已核对设备/绑定/epoch范围内，
由 C 在同一 Connection/事务中锁设备、重验完整前沿及保存证据、CAS领取。外层提交失败
绝不传出临时 claim。提交成功且连接已释放后才调用 C 的已领取发送口；该口重新确认
当前租约，查 B 同来源、必要时 POST、核对原异常已 resolved，再作 fenced 回执。
没有设备锁跨 HTTP，没有第二次 generic claim，也不修改已冻结正文/hash来强行重试。

此角色显式选择 `deliver-recovery` 并提供 `--confirm-recovery-contract` 才启动；
未确认的 worker 不访问数据库/网络。它使用现有私密遥测范围与 B 服务认证配置，
不会由普通应用/迁移自动激活，不发送 SMTP/飞书。准备窗口仍由原准备进程负责。

运行命令仅供已审定的隔离/获准运行环境内的 Python 3.14 使用：

```text
python -m app.modules.iot.telemetry_service --role deliver-recovery --config <本机私密遥测范围文件> --confirm-recovery-contract
```

不在宿主 D 盘 Python 安装依赖或直接执行此命令，不复制别人的认证。

## 实际验证

干净业务提交 `1c85ae22ede78b5c0e1ac3118e7fdc1123edbf0e`：

- PG 组合73 passed/0 skipped，含 A 九项新回归、C领取/发送、严格PG、原准备十四项。
  覆盖提交前零发送、提交失败回滚零发送、双调度器一次领取、错误epoch、坏原文、
  无新前沿时持久退避/重启，以及B已提交但响应丢失后按同来源确认。
  报告 `work/notify-subset-902b988815d945778f2c62c6126673bc/guarded-recovery-c121.xml`，
  SHA256 `4e7d485909d04375a36fb70db265b3e6c47685f247d4d9142a99272497ee23e8`。
- 完整后端1446个主用例，0失败/错误/跳过，覆盖率91%；mypy116源文件0问题，Ruff通过。
  报告 `work/a01-tests-20261026222407-79444/pytest.xml`，
  SHA256 `d63fab068e4f912ac99d986bd657a7595978e9e8741acc217968f7dc6c549dce`。
- 真实PG/C01、内部MQTT、四独立遥测角色及准备重启，然后独立恢复发送进程发现/领取，
  经TCP B写恢复并回读原异常resolved。停止该发送进程并重启一次，未重复投递，原正文/hash
  不变、attempt=1；同次运行继续验证迟到冲突→独立更正进程→B双ID追溯、控制执行ACK及后续数据。
  报告 `work/gated-a9aed80a7c84448d89612ba511419db9/runtime-composition.json`，
  SHA256 `b9bcb9150d2d75960975fce33ca3cd00757e4295a1c032a164f247d1e689474a`。
  `actual_guarded_recovery_background_tcp=true`、`guarded_recovery_sender_restart_no_resend=true`，
  子进程正常结束、强杀列表空，本轮临时容器/网络清理完成。

受控运行命令为 `check-gated-telemetry.ps1 -GuardedRecovery -AppImage <已核对缓存镜像>`。
保留 C 源身份门禁，未审新 C 头会被拒绝。此证明使用当前只读挂载源码，不能把提供依赖的
旧镜像标签说成已包含新源；同版可运行交付仍须在冻结后构建/核对。

## 未完成与边界

这次由后台进程发送恢复，不再由验证脚本直接 POST 恢复；它依然是隔离业务证明，
notifications_sent=0、complete_e1=false。此场景先用正常MQTT证明恢复，再证明控制后续数据，
不能颠倒时间说成控制导致了本次恢复，也不是实物冷库实验。

已经有 attempt 的冻结恢复遇到新的前沿时，C 仍保守 held，不重写证据。本轮未证明该组合
所有延迟/新接收交错都能自动收敛；持有状态不能写成恢复成功，需要记录并按审定处置。

当前候选还需同头独立审查、适用CI及其自己的合并许可。完整E1仍缺同版真实SMTP/飞书
授权实收、联合证据及最终第二机，M01—M05与实际参赛投递仍按共同签收表核对。
