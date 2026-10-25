# A57：恢复发送前原始证据检查的实际调用

2026-10-25，基于已发布 main `36cdc449d6f6b8bdb50b8cd0e74f2bacf97b4546`
（PR78精确162在C正式批准/当前检查成功和队长许可下合并）。本增量保留C
`be22199637aa6010b1cfe82940a106397c44e122` 的跨冷库PG回归，以及
`cd3707ad1ab3f88ee62a15d5c82e55d076255efe` 的恢复发前原始JSON复核。
合并时仅追加保留C已有证据文档，未覆盖既有报告；C生产子树等于cd3707a。

## 为什么必须检查

冻结后的恢复outbox不等于仍可发送。新接纳/坏接收、原异常或窗口数据不一致、
当前设备/冷库/epoch不符，都必须等待而不是误报恢复。C在A前沿锁内重新核对
冻结六字段、原始JSON与派生质量、原异常温度/时间/正文摘要和窗口规则。
`ready_at_check`仅表示那次检查时成立；释放事务锁后仍可能有新证据。

## 复用原入口，不另造运行栈

原 `--original-source-event-id` 准备模式不变。新增互斥的纯检查模式：

```text
python -m app.modules.iot.recovery_observation_service \
  --config <private-telemetry-scope.json> \
  --check-recovery-id <frozen-recovery-source-uuid>
```

最多14个互异UUID，配置设备与epoch必须完全一致。先只读核对当前作用域，
再读取冻结拥有者；结束发现连接后调用C自己的前沿检查事务，不在已持有
同设备锁的事务里另开连接。缺行/越作用域/冻结epoch不符返回held；错误不
打印原始载荷或真实配置。输出明确写 `not_send_authorization`；held或
retryable_failure退出2。该模式不写revision、不claim、不增加attempt，
不发B HTTP/邮件/飞书，不创建迁移、后台线程或数据库表。

## 实际验证与边界

Python3.14.8、缓存应用镜像与随机专属PostgreSQL18，先实际升级唯一链至C01/
metadata check。A调用、C窗口/更正/PG存储/原始证据前沿六组目标 **60 passed**、
无失败/跳过；包含原高温和窗口raw_payload与派生值不一致时held、实际旧B
规则、CLI只读检查、跨冷库冲突、更正不可认领。私有原始证据目录：
`work/notify-subset-59549a087b374d6cb5e6c8797962b06e/`，清理0/无容器网络残留。
完整回归及独立CLI的最终精确版本/hash以本分支PR/#6实际结果补充，不沿用C
机器或旧A头结果代签。

当前只是**发前检查接点**，不是发送器，也没有消除检查结束至网络发送之间的
竞态。B关联data_quality更正契约仍需作者实现/确认；未齐前恢复发送与晚到坏
证据更正保持禁用/held。双渠道终端实收、完整E1、最终同版第二机和M01—M05
仍未签收。不能用本地ready_at_check、当前CI或此文档代替这些出口。
