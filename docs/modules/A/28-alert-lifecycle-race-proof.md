# A28 告警并发补位前的独立证据

2026-10-21，北京时间。用户已把最小补位范围发给 B；截至本轮可读 #8 没有新交接
回复，PR10 回读仍 `0f107299790aca50fea5349d85a02379b1d58efe`。
这不证明 B 本机没有未上传成果。**本轮只增加 A 探针，不修改 B 服务或接管 B 分支。**

## 基线与用途

基于已上传 `e69094ff2c2bf81790a7402befdd8b18066d7c3c`，B 模块与上述本人提交
逐范围 diff 为零。A27 的 1058 项通过不包含本脚本；现有顺序测试没有覆盖下面并发。
探针只暂停真实 `get_alert_status` SQL 查询之后的执行，不替换状态、SQL 或提交结果。

这也是未来返修的验证入口：首个事务暂停后，第二事务如果无锁会先完成；如果采用
正确数据库锁，会由独立连接从 `pg_stat_activity.wait_event_type = Lock` 实际确认
其等待，然后释放第一个事务。不会强求两事务都拿到同一排他锁再过屏障而自锁。

## 实际运行（失败是被测缺陷，不是验收通过）

运行源：上述提交加未跟踪 A 探针。缓存依赖镜像内 Python3.14.8，PG18.6，实际隔离
`READ COMMITTED`。数据库只在 `test-db/freshlink_test/FreshLink-test` 精确守卫后
连接；每个向量只建随机 `a_lifecycle_probe_` schema 和六张 B 候选表，不跑原库DDL。

| 首个事务 / 第二事务 | 实际结果 | 安全验收应为 |
|---|---|---|
| ACK / ACK | 两个 created=True，2条 OPEN→ACK | 仅一次新确认，另一幂等返回 |
| ACK / recovery | 恢复先提交，迟到ACK使最终 acknowledged | OPEN→ACK→RESOLVED，最终恢复 |
| recovery / ACK | OPEN→ACK 与 OPEN→RESOLVED，历史链不连续 | 先恢复后迟到ACK拒绝，零新ACK |

三项的 `second_waited_on_pg_lock` 都为 false，`safe` 都为 false。探针 exit **1**，
不是通过。合成异常/恢复实际走 B 服务与 PG 提交；没有真实温度设备/邮件/App投递。
报告 `work/alert-race-20261021104736-61340/race.json`，SHA256
`b5f10c8ee83f76d58261317110a422249441c2c33af2380b171a82fbcccbb405`。
报告原文没有私密账号/令牌；work 文件不上传。三随机 schema 剩余0，临时PG容器与
所属网络清理exit0。首次只读 Ruff 检查因 CLI print 规范报错，改标准输出后规范通过；
没有放宽业务测试或全局检查规则。后续加源身份稳定守卫，清洁提交重跑另行记录。

## 可执行入口及失败区别

在项目目录使用 `powershell -NoProfile -File scripts/windows/check-alert-lifecycle-races.ps1`。
只使用现有缓存镜像，不下载安装，不使用宿主Python。探针不是默认CI/应用启动步骤。

- exit0：仅三项交错安全结果通过，仍不是完整 B 生命周期、锁超时/回滚或 E1 验收。
- exit1 且完整 race.json：实际业务安全断言失败；本次就是这一类，必须返修。
- exit2／无完整报告／清理失败：基础设施或探针未完成，不能假称复现或修复成功。

B 确认暂停重叠范围、提供未上传增量后，A 才实施最小共同原异常行锁和 HTTP 失败映射。
修复还须补回滚、锁等待超时、并发恢复重放、同时间/时钟回拨及权限负向，再由 C 按
精确新 SHA 独立审查。此探针不解决接纳前沿，也不授权正式迁移、合 main 或真实发送。

S0 已最小完成；20日20:00完整E1延期，27日功能/第二机冻结、30日及之前投递不变。
