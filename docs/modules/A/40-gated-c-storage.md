# A40：C 同连接接入候选与真实 PostgreSQL 证据

2026-10-22，范围为 A45—48 候选 + C 精确提交
`5603552718d64c36bf369d162b515dea9ce24150`。本分支是集成候选，
不是已合入 main，不升级原数据库，不启用自动恢复或真实通知。

## C 本人提交的独立验证

使用已缓存 Python 3.14.8 应用镜像、PostgreSQL 18、独立 internal Docker
网络、tmpfs 数据目录，C 源码只读挂载。严格限定 `test-db/freshlink_test`，
每测试独立 schema。九项真实 PG 均通过（没有跳过），包括新同 Connection
观测提交/回滚、恢复窗口提交/回滚、高温任务/风险/outbox 原子提交、
并发规则冲突和 JWT/HTTP 固定快照 CSV；另三项 C01 提案静态测试通过。

私有证据：`work/c-pg-234bf00f552a4bf2acb23f6fc373c442/tests.log`、
`result.json`。测试 schema 回读 0，专属 runner/db/network 全部清理。
首次环境配置用了不被 EmailStr 接受的保留测试域，尚未进入 PG 专项即报错；
改成模板示例域后上述十二项通过。没有把首次失败删去或归咎 C 业务。

## 实际 A worker 接点

`TelemetryDeliveryWorker(engine, GatedTelemetrySink(engine,
frontier_epochs=verified_epochs), frontier_epochs=verified_epochs)`。
receiver 与 delivery 必须使用同一已核验设备/epoch 集合；这里只提供显式库调用，
不改默认启动路径。C 不得把此示例当作已部署运行配置。

1. A MQTT receiver 完成认证、注册、绑定核验并持久接纳；它的 QoS ACK
   只表示 A inbox 落库，不表示 C 或通知成功。
2. delivery 短事务领取并核对租约/原信封，关闭 inbox 事务，再调用 sink。
3. sink 新开 PostgreSQL READ COMMITTED 事务，同连接先
   `begin_evidence_write` 锁 stream、核 epoch/绑定并推进 revision，继而
   共享行锁核验当前温度传感器、真实 B 冷库。锁次序 stream→device→warehouse。
4. **同一个 Connection** 调用 C `ingest_observation_on_connection_candidate`；
   不开 C Engine 包装事务。invalid/conflict 或非完整 ok 质量保留原文但同事务
   `mark_uncertain`，不将缺测当作恢复许可。
5. 退出事务成功提交以后才返回 C 回执；写入或提交失败统一脱敏为可重试失败。
6. A 再开短事务重核活租约，保存终态/前沿和元数据；如中途崩溃，原 reception
   ID 重放由 C 幂等回读，不能重复原始观测。不在 stream 锁内反拿 inbox 单例。

新增真实 PG 回归包含：真实 inbox→C→终态完整前沿、原文/重复、C 写后故障
整体回滚、commit 故障不返回成功、同设备锁等待、错 epoch/范围、绑定变化、
执行器冒充传感器、真实冷库缺失、缺湿度保留并 hold、异文冲突不覆盖原文。
临时 schema 中建表只是测试夹具；这不证明 C01 活动迁移。

初版组合全后端隔离回归 **1184 passed / 0 skipped / 93%**，含活动
A04→A05 测试库升级和 `alembic check`，证据
`work/a01-tests-20261022101723-35536/pytest.xml`。随后仅为 C 的 settable
Protocol 添加 A 可写投影，不改原冻结信封；最终投影版 Ruff/mypy 通过、
22 项专项（9 C PG + 3 C01 静态 + 10 新桥接 PG）重新通过，证据
`work/c-pg-6c9d35c9fb98473c83ff509fc754a4cf/tests.log`，清理成功。
初版全回归与投影后专项分别记录，不把旧源码全回归标成最终新源码全回归。

最终业务提交 `1df94d5f6ac6cd2f71d528de55b4aa921be8d1bf` 又独立完成全回归：
**1184 passed / 0 skipped / 93%**，证据
`work/a01-tests-20261022102537-36064/pytest.xml`，SHA256
`6f6c444b3b4f1bcf54d0eaa85e51c1bf0e2aeb49ab09017ce40c7f9429912486`。
迁移/model 检查通过；专属测试容器及网络均已删除，原 freshlink-db-1 未操作。

随后回读确认 PR44 **MERGED**，共同基础合并提交
`a03d14d026c786f993446bc27c4396ac8548cb0a`，仅合入被授权的 a35 精确头；
本集成候选及45—53未随44合入。下一轮启动需再读取最新 main。

## 仍未完成的必选项

- PR44 仅在当前精确头的正式 C 批准、适用 CI 和既有用户许可均满足后合并。
  A45—48 及本集成候选不继承该合并许可。
- C01 仍非活动提案，恢复冻结 stamp/原异常字段尚待 C 补齐；正式唯一链
  按 A04→A05→C01 审查协调，不以 create_all 放进生产启动。
- C 其他写入口、可信 B 规则/批次选择、恢复冻结及发送前复核仍须同连接接线；
  本 sink 不是全 C 写覆盖，也不自动解除任何历史 hold。
- 未跑本候选 MQTT→页面→通知完整 E1、两库三批14设备完整业务、第二机，
  SMTP/飞书授权实收与 M01—M05 最终签收仍缺。

最晚10月27日实际投递，25—26日成品与材料复核；原20日完整 E1 延期保留。
