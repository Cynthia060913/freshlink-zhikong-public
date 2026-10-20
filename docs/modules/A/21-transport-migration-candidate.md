# A 六表迁移候选：尚未激活

2026-10-20。A20 已说明六张持久队列/投递表未进入正式运行迁移。本候选补齐冻结DDL，
不是正式链发布，也不创建第二个活动Alembic head。不修改B/C业务、共享env或原应用库。

源码 `backend/migration_candidates/a04_transport_storage.py` 故意放在活动versions目录之外。
提议顺序为既有A03→审合后的B01→A04→C正式后继，**仍须与C确认后再激活**；
如已确认的C链先行，则在独立审查时明确调整父版本，不能硬插或产生平行head。
当前运行候选B01不等于批准有待修测试的PR10新头。

| 表 | 责任 |
|---|---|
| iot_telemetry_queue_guard | 单例容量/并发锁；迁移必须插入id=1 |
| iot_telemetry_inbox | 原始接收、预算、租约和下次重试时间 |
| iot_telemetry_ingest_receipt | C处理后的稳定回执和观测关联 |
| iot_telemetry_ingress_audit | 拒绝/重试人工操作的审计 |
| iot_event_delivery_checkpoint | 心跳/控制事件向B投递的持久检查点 |
| iot_event_delivery_retry_audit | 人工重试轮次与操作者审计 |

DDL显式冻结，不在迁移里动态import当前业务metadata或用create_all补缺；六表不抢占
C的观测、质量、outbox、窗口或B的告警所有权。降级仅列六表，但真实库降级会丢失
对应数据，不能按本测试授权自动执行。

测试清单：活动链未发现A04；冻结列/约束与A模型一致；降级仅六表；显式opt-in的真实PG
一次性UUID私有schema中运行upgrade、列/约束/索引比对、单例种子、错误预算/无租约拒绝、
连接池重开留存、downgrade/upgrade，并核对public表清单和alembic版本未变；清理只删
本测试确认创建的私有schema。未opt-in明确跳过；已opt-in不把连接或断言失败改成跳过。
实际结果以本阶段JUnit/PR记录为准，不能凭本清单宣称已通过。

## 本轮实际结果（10月20日14:11—14:13）

加入上述三个文件的同样内容后，在既有隔离入口实际运行全后端：Python3.14.8/
PostgreSQL18，pytest **994 passed**，JUnit实际994个testcase节点，0失败/错误/跳过，
覆盖率92%；不是仅收集测试。候选四项回归均执行，真实PG项显式opt-in，升级/列类型/
主键/约束名/索引名和列/唯一性、种子、错误状态、连接重开和降升/清理均通过。
活动链仍仅运行到候选B01，前后public表清单/alembic版本不变；本测试新增私有schema
已删除，并已清理整个唯一临时项目。原应用库、宿主Python和真实通知未操作。

私有原报告 `work/a01-tests-20261020141130-13696/pytest.xml`，SHA256
`c6e1b9c1fa4da3061dd972f11918d399bb6b49ab84d497c8de8d2a3a85532977`。
Ruff检查通过。首次994项报告及加强索引/主键断言后的最终报告均保留，不覆盖旧记录。
浏览器证据仍来自A20的a16a5ce专项，本次没有把它当新迁移版本的整套UI/E1验收。

正式启用还需：B安全修复新头的有效审查/发布；C确认唯一串行顺序；A04精确头独立审查；
共享Alembic metadata注册六表和C正式表；空库升级/已有A03保留数据升级/metadata一致、
精确版本实际运行后，再做完整E1。此文不授权对原数据库迁移、部署或真实通知。
