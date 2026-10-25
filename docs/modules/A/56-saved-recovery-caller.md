# A56：已存观测的恢复窗口调用与显式入口

2026-10-25：基于已发布共同 main
`7f16115c2b063ec0efe171418ca7e2a1e2ab6d4e`，保留 C 原作者提交
`5c41ae657f01504af35228a811bf382b1d3d1bae` 的同 Connection 端口与
`894c76b87ee9ab0af1a9f3373ba9a0c8cfc7e993` 的跨冷库冲突修复。
本分支是待独立审查的增量，不是已发布恢复功能或完整 E1。

## 具体接点

`GatedRecoveryObservationWorker.advance` 先在独立只读连接选择原 B 异常
对应的历史规则，结束该读事务后，再开启唯一写事务：

1. A `begin_evidence_write` 校验传入设备/冷库/epoch，取得设备流事务门禁。
2. 共享锁核对当前设备类型、绑定和冷库，禁止执行器或已重新绑定设备混入。
3. C `advance_saved_recovery_observation_on_connection_candidate` 在同一
   Connection 重读原异常摘要、观测、raw、质量和历史规则，推进持久窗口。
4. C 同 Connection stage；达到恢复条件时，A 核对连续终态前沿，冻结六字段。
5. 提交成功以后才返回 progress/staged；失败回滚 A/C 两侧写入。提交失败后
   不将驱动可能仍处于事务中的连接返回池；无变更的重复扫描回滚空门禁增量，
   不无故使既有恢复冻结戳失效。

`advance_page` 使用 C 持久接收顺序水位，排除原异常观测；limit=1 也能续跑。
每个批次的原异常独立推进；处理一个原异常不代表其它批次完成。缺证据、
规则、当前绑定、完整前沿时保守等待，不把坏数据当恢复。

## 显式有限入口（只准备，不发网络请求）

已迁移、显式初始化、核验作用域后，在受控应用环境中调用：

```text
python -m app.modules.iot.recovery_observation_service \
  --config <private-telemetry-scope.json> \
  --original-source-event-id <original-high-event-uuid> --limit 50
```

配置复用 `TelemetryServiceSettings`，设备与 epoch 必须完全对应；不读 MQTT
密码、不连 Broker、不自动修 epoch、不建表、不改迁移。每次最多指定14个互异
原异常，每个只推进一页（1—100条）。重复执行由持久水位续跑，无后台循环。
输出仅统计本地状态；held/retryable_failure 时退出2。配置/SQL异常统一脱敏。
没有新观测而输出空统计，不表示该原异常已恢复；以窗口/冻结及后续真实回执
另行验收。此命令会写指定数据库的恢复进度，原库/真实库运行须先取得批准。

## 本轮实际验证

Python3.14.8、缓存应用镜像、随机专属临时 PostgreSQL18，先实际升级唯一
Alembic 链至 C01 并运行 metadata check。随后运行 A 调用/CLI、C 恢复outbox、
更正与存储 PG 四组既有相关模块加 CLI 负例：**52 passed / 0 skipped**。
结果见本机私有
`work/notify-subset-3fb3f697a3494895aefc44514fb64121/recovery-subset.xml`；
cleanup exit0，容器和网络剩余均为空。部分测试使用每例私有 schema 建表，
不能把这些单元 fixtures 另称一套正式迁移证明。

覆盖：实际 B 历史规则/原异常服务接收、C 已存窗口、第三样本冻结、重复扫描、
错 epoch/设备类型、前沿未连续、C 写后失败回滚、提交失败后连接池可续用、
显式 CLI limit=1 连续三次在真实 PG 续跑、重复原异常拒绝、错误脱敏。
首次提交失败测试暴露连接池状态问题，修复后回归；不隐藏失败过程。

规范和两生产模块 mypy 实跑通过。完整后端回归须看本分支后续精确提交与
实际记录，不能用52项针对性通过代替全套/云端检查。

## 严格未完成边界

- 不发送恢复，不把 pending/staged 当 B accepted/resolved。
- 数据质量关联更正仍为 C `held_contract`、attempt=0；B F03 契约仍需作者确认。
- 恢复发送时刻的前沿新鲜度/并发顺序核验与真正 B HTTP 恢复回执尚须集成。
- 本轮 B 服务使用同一私有 PG 的实际函数，不冒充真实 HTTP 或两通知实收。
- 没有 SMTP/飞书外发、终端实收、最终冻结包第二机、完整 E1 或材料最终签收。

下一步只收敛恢复/更正最小缺口，保留原成果，不重做设备、控制、页面或迁移。
