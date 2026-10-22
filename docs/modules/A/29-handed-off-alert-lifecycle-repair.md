# A29：B 明确交接后的 ACK／恢复并发最小修复

2026-10-21，北京时间。B 在 #8 的交接记录 6307252259 明确暂停
`alerts/services/events.py`、`alerts/routes/lifecycle.py` 和并发测试，直至 C 对精确
新提交复审；B 继续通知适配/派发。A 正常取得并 merge B 的完整原提交
`12c5829b25c628efa4ea4b2c7b82fdb7e2b124c8`，没有覆盖或丢弃其 ACK 行锁成果。

## 版本与范围

- A41 基础：`b6ad3bd63766b93a2cc5d55c77cb80d409b6d86f`；继承 A40 的 C
  `f895e95b1df51c2fa8943be6dbaaa99bbe9e32b1`。B 交接前业务基线为
  `0f107299790aca50fea5349d85a02379b1d58efe`。
- 保留 B 后的合并点：`6d0fb4faea5ffd845b3c770e9dbdd481ffd198d9`。
- 修复与可执行探针提交：**`ae2a941b8f1f45195e7e70b48003173fdaf4bf4e`**。
  本文后续提交不改变受测业务/测试树。B 随后新交的飞书适配 049f1c5 **不在此组合**。
- 只修改两个已交接业务文件，增加一份真实 PG 测试，扩展 A 自有浏览器证明。
  不改变 F03 摘要/错误恢复语义、迁移、根认证、通知传输及 C 模块，不自动恢复。

## 修复约定

ACK 与恢复都先锁**同一原异常 `alerts_event` 行**，再读追加式状态历史，直到同一
事务提交或回滚。恢复事件和状态转换原子提交；重复恢复在拿锁后再次检查 source，
同文 duplicate、异文 conflict，不新增第二次 resolution。

PG 限定 `READ COMMITTED`，每次生命周期操作设置事务局部 lock_timeout=3000ms、
statement_timeout=5000ms；超时/数据库故障不伪装 404/409。ACK 返回 HTTP503 与
Retry-After30；恢复保留 F03 retryable_failure→503。已恢复后的 ACK 为409且零写入；
重复 ACK 幂等且零新增。两种通知调用均不放在行锁事务里。

新迁移时间取 `max(now_utc, latest_transition.created_at + 1μs)`，让相同时间或系统
时钟回拨不能把恢复排序到旧 ACK 前；不改写历史时间。

服务自己拥有干净的提交单元，允许 JWT 查询形成的只读 AUTOBEGIN；对 pending
new/dirty/deleted、显式 Session.begin、绑定外部 Connection 的 Session **先拒绝再
触碰事务**，不提交/回滚调用者的待写数据。这不是任意外部事务的可组合 API；调用者
仍不得事先 flush 无关 SQL 再交给该提交型服务。SQLite 仅保留顺序回归，不证明锁。

## 实际验证

环境只使用缓存镜像（Python3.14.8、PG18.6），随机专属临时数据库/内网，真实配置、
原库和宿主 Python 未修改。所有本轮临时容器/网络已按所属项目清理。

1. 全后端 **1071 passed，0失败/错误/跳过，覆盖率93%**，97.21秒。
   运行源为上述6d合并点加随后保存到ae2的三个后端文件；受测 backend 树为
   `b9947badd606748632966e52c62705ccd86ec118`。
   `work/a01-tests-20261021112324-73468/pytest.xml` SHA256：
   `a0bd694ffb702499a23dcdc151858d30cc965537dcdbf405457c891d4758a4cf`。
   计数依据 pytest 控制台和1071个唯一 testcase 元素；此环境 JUnit 根 tests 属性为
   1329，与元素数不一致，不能据该属性夸大测试数。已有两个框架/无前端目录 warning。
2. 新增13项真实 PG 用例：ACK/ACK、ACK/恢复、恢复/ACK 三交错；并发同文/异文/
   不同source恢复；ACK/恢复实际锁超时与同操作重试；写转换失败原子回滚；实际
   commit后丢响应的幂等重试；2099旧时间后的连续排序；调用者事务拒绝；实际根
   JWT匿名401/普通人403/管理员故障503+Retry-After与恢复成功。
3. 精确**干净 ae2** 独立重跑 A41 三交错，3/3 safe=true，全部实际观察到 PG Lock
   wait；probe_exit=0、cleanup_exit=0、残留随机schema=0。
   `work/alert-race-20261021113148-72196/race.json` SHA256：
   `0cb19a71c766fc55775c95adceb1b7fa8c37c25c18aa67241e14330e171a8982`。
4. 精确**干净 ae2** 真实 Chromium148/Playwright1.62.1 页面→HTTP→PG证明：管理员
   点“确认告警”→一条ACK、同操作HTTP重试不新增；真实HTTP合成恢复→两条连续历史、
   页面显示恢复、迟到ACK409；普通用户只读/POST403保持零写入。未拦截伪造API结果。
   同次原有实际MQTT控制链通过：一次设备执行、一条保存ACK、采样温度下降。
   前端沿用同树 `d085bd5ce93ac943e89c3e07472f9f6beb1d9563` 的 A40 构建。
   `work/control-browser-20261021113051-57868/browser.json` SHA256：
   `104312ef62bf926efcd1700185c8b6f7214d5b4ff4d0af335f21beab0395327e`；
   server.json SHA256：`1069d6580e61ba99ea8ec8444ff4b3e62faff73664fa1a7f4436f0ba9a9d5f6e`。
   dirty_before=false、proof_exit=0、cleanup_exit=0，截图仅私有work留存。
5. 两业务文件 mypy/ty通过；两个业务文件+新测试 Ruff check/format通过；Node与
   PowerShell探针语法、diff检查通过。完整mypy仍有C outbox_candidate.py:164
   no-any-return，已直接交C #9评论6307245406，不称全仓类型检查通过、不放宽规则。

## 待验收与接续

此为待 C **按精确新头**独立审查的候选，不是自动撤销旧审查阻断，也没有发布 main。
浏览器输入明确为 synthetic，不是 C 自动异常/恢复、历史CSV、真实两通知或完整 E1。
正式唯一 B→A运输→C 迁移链、共同运行入口、A24接纳/连续终态前沿仍按各自门禁推进。
B 通知分支继续正常开发；先合通知纯增量也不能视为允许改回已暂停并发范围。

20日20:00完整E1未达事实保留；27日功能/失败/第二机冻结、28—29材料、30日及之前
实际投递不变。现在优先解除这份明确小包与共同实链，不增加可选算法或装饰。
