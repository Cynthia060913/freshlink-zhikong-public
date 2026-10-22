# A27 当前 C 同事务端口与共享根专项

2026-10-21，北京时间。本文是隔离候选验证，不是 main 发布、正式 C 迁移、
自动恢复放行或完整 E1 验收。S0 最小出口已关闭；10月20日20:00完整 E1 未达，
27日前必选功能/第二机冻结、28—29日同版材料复核、30日及之前实际投递不变。

## 1. 精确来源及改动边界

- 基础为 A/B 页面候选 `a4af1500e3b50ae13bbaa2b961876da7c09d6c7a`。
- 普通合入 C 本人已上传 `f895e95b1df51c2fa8943be6dbaaa99bbe9e32b1`，
  合并提交 `5757bcf10bbe0e6a0c4d4beca11bacb7d08b3e98`。C 事务服务来自
  `6e2c1de71fb71c3bb67c9731a311c6d1729c62d8`，PG 用例来自
  `3dd61a3291420e8435cae55b90619a86a463f06c`；不复制改写 C 业务。
- A 修复提交 `da45d774744e00f64750361d968da1b97abc8b75` 仅改共享根和
  `test_c_root_wiring.py`：挂载 C 自带管理员拒收审计路由，更新四个观测读取
  路径计数，补缺表 503/Retry-After/不自动建表回归。鉴权沿用 C 原依赖。
- B 仍为 `0f107299790aca50fea5349d85a02379b1d58efe`；并发 ACK/恢复两项
  阻断没有被此候选修复或放行。C 最新只读轮询页面已带入源码；构建不等于浏览器验收。

## 2. 实际失败和修复后回归

缓存镜像 `sha256:779cd8742c61921eb38c82d97c321bbf1b7f8a8e05f4326a006adeae6822b435`
提供 Python 3.14.8 和依赖；实际被测后端以只读挂载的本候选源码为准，
不是宣称该旧镜像已包含新 C。每轮独立随机 Compose 项目、tmpfs 数据库、内部网络；
无真实 SMTP/通知配置，无宿主 Python 安装/卸载，无原应用数据库连接。

1. 合并后首轮：1057 collected，1055 passed / 2 failed。A 共享根漏挂审计导致
   404 而非匿名 401；旧路径计数断言期待 3、实际新增 latest 后为 4。
   不删除 C 用例，不跳过检查。该轮 5 项 C PG 用例已经实际运行通过。
2. A 修复后完整复测：**1058 passed / 0 failed / 0 errors / 0 skipped，93%**。
   实际后端树 `52770510f48f8b7d0ffcdce819c7c804d0c68f2a`；运行时为上述合并
   提交加两处 A 修复工作树，随后原样保存为 da45 提交。
3. JUnit：`work/a01-tests-20261021103036-18656/pytest.xml`，SHA256
   `431c93c65d09bd17c6419356ef1c091a5777a6adcd24225b26eae887a810d592`。
   XML 实际 1058 个 testcase；不是把 suite 的子测试聚合数另外算新增测试。
4. 两处 A 文件 Ruff check / format --check 通过。首次只读容器检查因为 Ruff
   默认缓存需写入而失败；加 `--no-cache` 后通过，没有改宿主或放宽只读挂载。

## 3. C 请求的 PG 结果和不能外推的结论

- 实际服务器 PostgreSQL 18.6；临时服务器 `SHOW default_transaction_isolation`
  回读为 `read committed`。5 项 `PostgresStorageCandidateTests` 无跳过通过。
- 新通用 staging 用例：同一调用者连接先做 duplicate 和 conflict；SAVEPOINT
  后外层事务仍可使用；外层 rollback 后新连接冲突行 0；再次提交异文冲突后为 1。
  它实测的是通用 outbox staging，不冒充完整自动恢复、A 设备锁或发送线性化证明。
- C 的恢复 Connection 端口和外层 rollback 用例也在完整回归中通过，但该专门用例
  使用 C 自带 SQLite fixture，不能把它重新标成 PG 恢复链路。
- PG 各测试随机 schema 的非 CASCADE 清理均未报错；两轮临时数据库容器与所属
  网络均由入口清理成功。只读复查原 `freshlink-db-1` 为 healthy。
- 正式 Alembic 在本候选仍止于 B01；C candidate 表由严格测试隔离 fixture
  创建，不是正式迁移或生产 startup 自动建表。缺表依旧明确 503，不伪装空结果。

## 4. 下一门禁

前端已按本轮成功 OpenAPI 重新生成 SDK，新增 latest 与管理员拒收审计类型；
Bun 1.3.12 锁定安装 436 包、Biome 87 文件、Vite/TypeScript 构建均通过。
接口 SHA256 `a079e98b9430fcd17e29395fca6b46c10038b03907a6e5bbe0807a62d8c2b84c`，
报告 `work/a01-frontend-20261021-103323-46112.json`，实际产物目录
`work/frontend-bundle-a7af451a247c42c1a14c71e723204600`，含新 telemetry 页面。
安装仅在一次性 Linux 依赖卷，宿主 Python/Windows 依赖未更改。
本轮未重新运行 Chromium/MQTT 浏览器全链，不沿用旧 C58 浏览器结果冒充当前 C。

A 下一小包仍是 A24 接纳序号/连续终态前沿和同设备事务锁及独立 PG 负向并发；
C 本次端口解决了“自行 begin/commit 无法共用连接”缺口，但不自动补齐 A 锁、
所有直写门禁、晚到失效及发送前复核。B 先修 PR10 两个并发问题并交新头，
C 按精确头复核。正式唯一迁移、两渠道授权终端实收、14 设备、第二机和
M01—M05成品继续分开验收，不因新增绿灯或测试数宣称项目完成比例。
