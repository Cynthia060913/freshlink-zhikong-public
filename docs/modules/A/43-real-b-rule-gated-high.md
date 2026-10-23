# A43：实际 B 历史规则 → C 门禁候选 → B 接收回读

2026-10-23，A 的共享调用方增量，不改 B/C 业务源码，不另建迁移。
依赖 PR54 的精确 d68；本增量不带入 d68 的既有合并许可，不修改该已许可头。

## 输入 → 处理 → 输出

`GatedHighWorker` 从 C 已存观测/待评估任务取得可信设备与冷库，不读取设备自报
批次。显式传入1—14设备/epoch范围。实际 B 冷库批次查询按观测采样时间选择
唯一有效批次，再调用 B `select_rules_for_observation`，不是选择器 Mock。
用该历史规则和 C 原文重算质量并构造稳定来源ID，调用 C 的
`stage_gated_high_candidate`；A锁/epoch/revision与C outbox/risk/job同事务。
B规则读取事务先结束，锁内没有B网络请求。未通过核验的结果保留pending/unknown，
不虚报normal，也不自动发送恢复。

**明确只支持第一库单批次高温出口**：0或多个有效批次、超100批次范围、无规则/
非高温/坏证据保留待处理，不偷偷只选第一批；完整两库三批还需要C的多批次
job/原子候选契约。读取历史规则临时失败可重试，错epoch/写故障不会留下半个候选。
显式run_once上限100，不安装定时器、自动worker或真实通知配置。

## 版本与验证

- 保留 C `9f049ead31f5fa7789ca84a8aaa679f5725b18aa` 历史，正常合并提交85c20af。
- A业务核心 `632f9e8295eb6c0d232131b369a60a7d427df92d`：8项真实PG针对性通过，
  实际B规则/C门禁提交和重放、0/多批次不丢任务、无规则/非高温不伪正常、
  规则读失败、C中途异常整体回滚、错epoch、坏证据、有界扫描。
- 同业务树完整后端 **1206 testcase通过/0跳过/93%**，实际Alembic C01/model
  check通过；Ruff及mypy app（97 source files）通过。与随后证明脚本变更分开记录。
- 清洁 `44a657a54ae84d8e51fe5cc4c65f52474b311ba0` 实际认证MQTT→A持久接纳→
  C观测→真实B批次/历史规则→C门禁outbox/risk/job→C可靠投递协调器→
  **真实B根HTTP201接收、按来源200回读/摘要核验** 通过。另连接 NOWAIT 能取得
  A stream锁，确认B HTTP不在A证据锁事务中；重复评估/投递不新增第二条B事件。
  保留此前重启/数据库故障回滚持久重试、原文、缺测hold、匿名401/同快照CSV断言。
  此HTTP为ASGI TestClient，真实路由/鉴权/PG，不是TCP/浏览器。真实外发为0。

首轮证明适配器未明确JSON Content-Type，实际B根接口返回422，未报告通过；
保留失败日志并补明确Content-Type及只含字段路径/类型的脱敏失败诊断后复测成功。
修的是A证明器，不放宽B DTO、不Mock B接受、不修改/重置原业务数据。

## 私有证据（不上传work）

| 项目 | 文件/结果 |
|---|---|
| 8项PG | `work/high-pg-fa0b52049e3a43109f585358e467173e/tests.log`、result.json；exit0/清理成功 |
| 全后端 | `work/a01-tests-20261023122235-55704/pytest.xml`；SHA256 `0c2b1d9cdb0c3b8900f522290d376d59c66c5190d20c40f3a4587adde8e3efe6` |
| 清洁实链 | `work/gated-2d6fbacac4fb405c8ae5ec7b70d99208/gated-telemetry.json`；SHA256 `2c6d64b8efc2384478a2fd7890805ff0d2e6d3f24f3193dfa56fe7d0e7aafbb3` |
| 首轮失败 | `work/gated-ed2b1fb662e14722a13d226784ea39f3/runner.log`及cleanup.json；test1/清理成功 |

JUnit实际testcase/stdout均1206，失败/错误/跳过0；不用tests属性的subtest膨胀计数。
所有本轮专属容器/网络已独立按精确前缀复查为空；原库/宿主Python未动。
Compose重复停止已移除one-off runner提示仍保留，cleanup退出0且另查无残留。

## 不能据此签收的项目

不是完整E1：多批次、正常/恢复写入口与发送前冻结校验、两独立授权终端实收、
14设备完整业务、同版控制后数据/浏览器、第二机和最终材料仍未通过。
C所有写入口门禁/并发绑定变化还需专项核验。本增量不激活后台外发，不继承
PR54的合并许可；新头必须同头独立review/适用CI及具体许可。

下一最小协作：C补多批次与恢复的真实caller事务端口；B整合55/56窄修并完成
已提交事件→独立attempt/持久派发及私密双终端准备；A把可用端口接回同版运行器。
可评估单体应用内同Connection调用B服务并以savepoint保持外层事务的最小恢复
方案，避免持A锁网络外发；这仍是待三方确认/实际PG验证的方案，不是已实现接口。
