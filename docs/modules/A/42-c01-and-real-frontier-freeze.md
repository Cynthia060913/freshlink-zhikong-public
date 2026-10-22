# A42：唯一 C01 候选链与实际 A/C 冻结验证

2026-10-22 晚，延续 PR54，不另建平行集成链。**候选不等于 main 发布，
不授权升级原数据库或启用自动恢复/真实通知。**

## 版本与实际变更

- C 精确来源 `bad804c8dcd2d9656f33f23797b1590b703c2be5`，正常合并保留作者历史，
  合并提交 `7ab3c85`。C 的观测/恢复业务未重写。
- 新实际锁组合测试提交 `eb9df3e39fa1d9b1984b6e9767e1ec375765135e`。
- 唯一 C01 活动**候选**接线 `9d437927dd7f862e2dd28993d86716c5206f0962`：
  `A04 → A05 → C01`，注册 C 两份 metadata；十张表与六个可空冻结字段来自 C。
  静态测试要求活动副本 upgrade/downgrade AST 与原提案完全一致。
  不 seed epoch、不清 hold、不在 startup create_all、不启用后台外发。
- 联合 MQTT 验证去掉显式 proposal DDL，实际 `alembic upgrade head`/`check`
  后才创建合成业务夹具。缺完整 C01 schema 或已有业务数据则拒绝夹具写入。

## 已实际验证

1. C 原独立头 bad804c：受控 Python3.14.8/PG18，10项PG+3项提案静态全过。
   新第10项使用实际C行/回滚/重开连接，但 A 前沿判断明确 Mock，不能外推锁组合。
2. eb9df3e：29项专项全过。新增5项确实调用 A `begin_evidence_write`、
   完整前沿读取和 C 冻结端口，不 Mock A 前沿；同设备另连接在锁释放前不能写，
   事务回滚同时撤销 revision/冻结字段，提交后新连接可见；同事务重放幂等。
   新写 revision、缺测坏证据、错误 epoch、不同冻结戳均不能复用/覆盖旧戳。
   原异常 delivered、恢复 pending 是**合成 outbox 状态夹具**，不是实际 B 送达。
3. 9d43792 对应业务树完整后端：**1193 passed、0 skipped、93%**；空库实际
   Alembic 到 C01、model check 无差异。实际链测试从 A03 旧用户/设备开始，
   检查 A04边界、A05零行、C01十表和六可空列，C降回A05保留A epoch/revision/
   hold，重新升级和model check通过；再降A04/重升，旧用户设备与public数据不变。
4. 清洁 9d43792 实际认证 MQTT→A原文提交/ACK→C同事务持久化→JWT根HTTP/
   raw/固定snapshot CSV再次通过：3接收审计/2观测、重复幂等、receiver重启、
   C故障整体回滚与A持久重试、缺湿度保留并hold、匿名401。C表全部来自候选迁移，
   **不再以测试 DDL 替代迁移**。HTTP为ASGI TestClient，不冒称浏览器/TCP验证。

## 本机私有证据

| 证据 | 路径与SHA256 |
|---|---|
| 实际锁专项 | `work/freeze-pg-b725f1f1833b461ca8340397626206a9/tests.log`及result.json |
| 全后端 | `work/a01-tests-20261022204323-74004/pytest.xml`，`8b88403cb5561dd1e08070e4f5b5684d937ecdfe95049d6bc03f2516639a4e3e` |
| 清洁MQTT组合 | `work/gated-dd04fa9bfbda48f5b65d5f6d4ca54870/gated-telemetry.json`，`e58a819dcf80cf6ce5a141332ba5a9a440fde1f81e07ebab93a16d15ae57f34c` |

全回归 stdout 与 XML testcase 实际数量均1193；XML tests 属性却是1465，未使用
该属性提高测试数，也不据此宣称额外验收。零失败/错误/跳过按实际结果记录。
work不上传；材料摘录须脱敏。所有本轮随机专属容器/网络已清理，精确前缀复查无残留。
Compose仍出现重复停止已移除one-off runner提示，cleanup退出0且独立回读为空；
不隐藏提示，不清理其他资源。原数据库和宿主Python未动，通知发送为0。

## 仍缺与接续

PR54新范围/新头必须重新交C独立审查，不能沿用918批准；新C01未合main，
不将本轮测试DDL消除误写为正式发布。PR46转main后原批准撤销，待同头重批，
不绕过。真实B历史规则选择/高温投递/恢复与发送前核验、完整C写门禁、SMTP+
飞书手机端授权实收、14设备业务、浏览器、第二机、最终材料与实际投递仍未签收。
上述29项、1193项和MQTT分段不能拼成完整E1。

复测使用现有 `scripts/windows/check-a01-acceptance.ps1` 与
`scripts/windows/check-gated-telemetry.ps1`，仅选择兼容缓存镜像；缺缓存停止。
无需对原库运行任何迁移。下一共享出口是同头审查后明确发布唯一候选链，并由
C/B补实际规则与通知派发调用，最后按同一版本验一库完整E1。
