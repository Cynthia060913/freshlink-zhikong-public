# A30：A24 接纳前沿候选与同组合采集/页面/CSV 实测

2026-10-21。继承 PR42 `6ed501f13fd668412ea999b618a97ba188f7db1e`，保留 B12c
与 A29 并发修复。本文是精确候选的实现/测试记录，**不是正式迁移、自动恢复放行、
main 发布或完整 E1**。不把先前 A04 批准延伸到新增流表。

## 实现最小范围

`stream_frontier_models.py` 独立 metadata 的 `iot_stream_frontier` 与
`iot_stream_admission`，尚未注册主 metadata/Alembic，不在启动时建表。
缺表、缺初始化或世代不符均失败关闭。`origin_verified=False` 默认；只有受控测试
显式 seed 为真，未实现正式世代初始化/轮换/清除 hold。

`stream_frontier.py` 接受调用者已开启的 PG READ COMMITTED Connection，自己不
提交、不调用 C/B、不做网络请求。队列单例→排序设备流行锁的顺序保持一致：

1. 同事务接纳 raw、分配设备内连续序号；回滚不占序号，同接收同文复用。
2. C 在 A 事务外先提交。A 取得自己的短事务、复核原租约/指纹后，原子写终态
   回执与 ledger，只推进连续前缀，不能 `max(终态)` 跳过第一项 pending。
3. `read_complete_frontier(connection, device_id, warehouse_id, epoch)` 在持锁状态
   要求可信起点、同绑定/世代、非空、admitted==settled、ledger数量一致且无hold，
   才返回不可变 FrontierStamp。`stamp_is_current(connection, stamp)` 可复核失效。

invalid/conflict、已知拒收、ID跨设备重用、队列满/重试耗尽等保守持久 hold；
裁剪旧 audit/receipt 不清除坏证据。流 ledger 每设备每世代上限10000，满后hold，
不静默删账本。旧未跟踪记录不会自动收编为完整。锁/语句等待5秒有界。

`TelemetryReceiver` / `TelemetryDeliveryWorker` 的可选 `frontier_epochs` 是复制的
1—14设备可信映射；接收侧须与实际已验证设备范围完全相等，未知投递不调用 C。
默认 None 保持原运行路径，不偷偷启用未发布字段。

## 真 PG 专项与全回归

核心代码/测试源 `a73e98dd753703f418ed6b0a1ed739ac337073c1`；全后端 **1096 passed，
0失败/错误/跳过，覆盖率93%**，105.39秒。证据
`work/a01-tests-20261021114544-74552/pytest.xml` SHA256：
`9e1fac102c059b3d3ccaffe9266519eb4aaa44760b9cd9957ca18463ab534ab3`。
业务树 `backend/app` 为 `089280fa859c6422c31a32c9801c9a7cbc45a521`。

22项 A24 PG +24项 worker 的定向 **46/46**，证据
`work/frontier-tests-20261021114431-34256/frontier.xml` SHA256：
`084aad810ba48ea21821c774250a8dd3e3bc367194a41657235671aebc2fd15d`。
涵盖回滚无洞、两写者、乱序终态、接收重放/冲突双方hold、拒收/容量/旧账本/缺表/
租约过期、实际 C 提交后 A 检查点失败的同ID重放、持锁冻结与实际PG并发等待。
四个 A 业务模块 mypy/ty及六文件 Ruff 通过，不能代替仍待 C 修的全仓类型问题。

专项还实际调用 C f895 的 `stage_eligible_recovery_on_connection_candidate`：
四条 A 接纳→C 实际入库→A终态，三份恢复样本形成真实持久窗口；在同一 A 设备锁/
同一 Connection 内冻结，外层 rollback 同时撤回原事件 delivered 标记和恢复outbox，
重试再提交才落两条。原事件 delivered 在此仅为**测试前置模拟**，不是 B 实收。
后来接纳的早时间坏行仍 pending、已知拒收两种情况在 A 前沿就被挡住，不调用 C 冻结。

第一次新专项样本缺 humidity，C 严格质量检查拒绝冻结；修正 A 测试样本包含实际温湿度
后46项通过，未改 C 源码、放松质量或把第一次失败抹去。

## 同组合真实浏览器：不再只是构建/SQLite/函数结果

干净可执行源 **`4f52b05fcfc258998271c18bed599222639d6f71`**；相对上述全回归源
只扩展 A 证明脚本，`backend/app` 树完全相同。前端未改，使用 A40 已构建同树
`d085bd5ce93ac943e89c3e07472f9f6beb1d9563`；Chromium148/Playwright1.62.1。

同一次临时组合实际跑以下链，不伪造 API 响应：

- 连续 MQTT→实际 A 注册/仓库绑定→持久 inbox/投递→C PG：本次24份业务观测。
  171次投递完成包含传输重放，**不是171份业务样本**；delivery failures=0。
- 真实 C 页面筛设备，显示历史/原文与真实接收记录；实际固定快照24条，CSV24行，
  对全部 HTTP 分页取齐观测ID，和CSV逐ID集合对拍相同；持续新采样不改变旧快照。
  匿名历史HTTP401。没有改 C 页面或 C 数据服务。
- 同次管理员控制→真实MQTT设备执行1次/保存ACK1条；重放/刷新保持，采样由
  10.002727降至9.922548℃；普通用户控制403。
- 同次真实B页面管理员ACK、幂等、合成恢复后迟到ACK409/连续历史通过；**B异常及
  恢复输入仍是 synthetic**，不称为 C 自动触发，不拼成完整E1。

`work/control-browser-20261021115152-78144/`：browser.json SHA256
`9fc43893d3f819b7e97236782ce29c866191599c7e19ac7d890eac7e3e5ced25`；server.json
`f8dfcbd923c9146198cdab969249695cff5ead2cb459aa88acb6c8af56dbf8a6`；CSV
`0c8da87a89553e9d2a7502e6149e4911395a0888e6ff143944f76b90572fae5d`。
dirty_before=false、proof_exit=0、cleanup_exit=0，专属容器/网络均清理。
使用候选B01迁移与**显式 A/C 测试DDL**，不是正式 A04/C 发布链或第二机交付。

## 下一步只收敛必选，不继续堆可选功能

C按精确PR42头复审并发小包；A24新增字段/锁序也须明确复审。C可直接对拍上述
Connection签名与真实PG端口证据，继续 C 自有恢复/冻结入口：仍缺所有 C 直写门禁、
冻结前沿持久关联、发送前复核/晚到失效与发送线性化点。A不会把本次测试当作这些
均已实现；**自动恢复/外部投递保持禁用**。共同正式迁移仍由唯一集成者协调，不自动
新增平行head，不在生产 create_all 或假填 origin_verified。

B继续修通知review5594680226的三项实际问题、派发/重试及两渠道私密配置。队长已
指定 B 负责配置并确认收件；**不是已发送授权或已实收**。完整一库E1、14设备/第二机、
同版M01—M05仍待。按[最后一周收口](../../team/FINAL-WEEK-PLAN.md)在27日前形成
完整可提交成品，20日E1延期保留，原30日不当作开发缓冲。
