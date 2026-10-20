# A 六表迁移候选：尚未激活

2026-10-20。A20 已说明六张持久队列/投递表未进入正式运行迁移。本候选补齐冻结DDL，
不是正式链发布，也不创建第二个活动Alembic head。不修改B/C业务、共享env或原应用库。

源码 `backend/migration_candidates/a04_transport_storage.py` 故意放在活动versions目录之外。
顺序为既有A03→审合后的B01→A04→C正式后继。C于10月20日在PR34的
[精确头评审](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/34#pullrequestreview-5581672494)
接受该拟议顺序，但**正式激活仍须B01审合、A04本身过审和同版迁移验证**；
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

## 通用CI与严格PG专项的边界（10月20日新增）

原41b977把本专项挂在通用inbox开关上。通用CI在宿主runner运行、DB不是专用
test-db/freshlink_test，因而实际出现1失败/989通过/4跳过；C据此提出修改要求，
不能用本机994通过抹去该红灯。当前改为独立 `FRESHLINK_TRANSPORT_TEST_POSTGRES=1`，
只由严格临时Compose显式启用，原容器/账号/库/禁外发/自有schema清理守卫全部保留。
通用inbox开关不启用本专项，并有独立回归；通用CI的明确未启用跳过不是PG通过。

遥测专项workflow增加相关文件触发和严格临时PG步骤：实际运行完整隔离测试后，
检查唯一新报告中本PG testcase确实执行且没有failure/error/skipped，同时记录实际
执行源码、PR头和JUnit哈希。缺case或跳过即红灯，不靠仅减少通用CI报错签收。
新头的本地与远端结果分别记录；本段说明修复方法，不先宣称新CI已通过。

本地修复后的同样内容已在专用Compose中实际运行：995个pytest/JUnit testcase均通过，
0失败/错误/跳过，覆盖率92%。唯一的真实PG用例确实执行且没有skipped节点；新增回归
证明通用inbox开关不会误启严格迁移专项。唯一临时项目已清理，未操作原应用库或宿主Python。
原报告 `work/a01-tests-20261020150331-37996/pytest.xml`，SHA256
`4e863fb27fb64934973da0e72bdd575677001919b332666155ba68b76442899f`。
修复后的远端通用门禁与严格PG实际执行仍须在新提交上回读，不沿用本地结果签收。

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

14:22—14:24又校验并修正版本守卫：要求数据库版本与唯一活动head一致，且B01位于
该head的祖先链，不硬编码“head必须永远等于B01”，不误阻止未来合法C后继。
同样修改内容再次完整运行994项通过/0失败错误跳过/92%，四项候选测试实际执行，
私有schema和唯一临时项目均已清理。此轮还没有未发布C后继的实际迁移证据。
最终报告 `work/a01-tests-20261020142204-19608/pytest.xml`，SHA256
`0e04ae2cc776fc20576dcee4610ff3af9d58bcc655ba11b529a9cbf3c7f4fb16`。

正式启用还需：B安全修复新头的有效审查/发布；A04精确头独立审查与门禁通过；
共享Alembic metadata注册六表和C正式表；空库升级/已有A03保留数据升级/metadata一致、
精确版本实际运行后，再做完整E1。此文不授权对原数据库迁移、部署或真实通知。
