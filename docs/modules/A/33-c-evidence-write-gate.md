# A33：C 写入门禁端口候选（默认不启用）

响应 C 在 #5 评论6314762395的接线请求。继承 PR44 的共享A04候选，但不修改
其送审头；本包只增加A公开端口和A测试，不替C重写其服务、不启用自动恢复。

## C 可据此实现的准确入口

`app.modules.iot.stream_frontier.begin_evidence_write(connection, *, device_id,
warehouse_id, epoch) -> int`

调用者先在 READ COMMITTED 的 PostgreSQL 上开启外层事务；**在读取恢复证据或任何
C写操作之前**调用此端口，并将同一个 Connection 传给全部本地C操作。它取得与A接纳/
终态/冻结相同的设备stream行锁，核对当前设备绑定、epoch，推进 evidence_revision。
锁与版本增量由外层一起提交/回滚，不自行commit，不开第二个Engine事务。

待处理接纳可以进入写门禁（不能要求 admitted==settled，否则队列无法推进）。
写完后才能用 `read_complete_frontier` 核完整前沿，冻结时保存**写后**stamp及原异常ID。
只读查询不调用写门禁；幂等路径可保守增加版本，但不要轮询只读时无故增加版本。
所有观测/reception/invalid/冲突、风险/评估outbox、恢复窗口与冻结路径均须覆盖；
不能只包正常观测。坏证据还须在同事务调用 `mark_uncertain`，持久hold，不能自己清掉。

禁止：持stream锁后反向申请inbox单例；锁内B HTTP；无外层事务；独立Engine写入后
再临时上锁。批量设备需固定排序逐个锁，或由A扩展明确批量端口，不能任意顺序。

## 实际验证

缓存 Python3.14.8 / PG18.6，严格临时PG入口和随机独占schema。最终
`work/a-write-gate-20261021195005-24776/gate.xml` **27/27、零跳过**，含22原前沿回归
与5个新增门禁案例：

- 同一事务版本增加，外层回滚后原stamp不变；提交后无新A接纳也使旧stamp失效。
- 写者持锁时，另一真实连接的冻结读进入 `pg_stat_activity` 的Lock等待；分别验证
  提交和回滚后读取正确版本。
- 冻结者持锁时，另一真实连接写者等待；冻结事务结束后写入提交，旧stamp失效。
- 无外层事务、错epoch/仓库/未知stream及设备当前解绑都拒绝，版本零变化。

最终XML SHA256 `3ca8febc9bc6af6bad4fdf8ab0fc09eff8aa1418b97efd8dbd1de5e723fd073c`。
test_exit0 / cleanup_exit0；本轮临时容器/schema/网络已清理，原应用数据库未操作。
Ruff/格式、mypy app93文件、ty app通过。首轮fixture导入触发F811，改显式fixture包装；
类型检查发现RowMapping返回Any，改实际int验证后复测通过，未放宽门禁。
上述不是全后端新1108项通过，不能与PR44的1103相加；也不是C全部写入口已接线。

## 仍阻断生产启用

A24两表/受控初始epoch尚无活动正式迁移；本端口缺stream会拒绝，不自动补建/补种。
设备重绑定仍需共同锁策略；C冻结记录新增关联/晚到失效及B已受理后的更正、网络请求
与后续坏证据的线性化仍未完成。读取一次stamp再无锁发HTTP不能宣称安全。
先交C按此候选小包审查及实现本角色Connection接线，再共同验收，不自动开启恢复外发。
