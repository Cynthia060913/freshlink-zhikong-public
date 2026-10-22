# A→B 设备离线／恢复事件可靠检查点（待审候选）

2026-10-15，继承PR14 `96634331b7d1b9cb080d5fca6cb8c0dc843e53b7`。
不改B/C源码、正式迁移、应用运行配置；不发送邮件或APP，不代表完整E1。

## 映射和调用

`deliver_to_local_alerts(engine, source_event_id)` 是服务器内部一次投递入口，
不是公共写API或自动常驻worker。可信身份/设备状态由现有心跳worker建立。

- 只投已经持久保存的`pending`事件。`held_unbound`、空warehouse不投，
  后续绑定不能把旧事件改绑；`failed`需要操作者明确处理，不自动重置。
- 同一原`source_event_id`、device/warehouse、kind、原发生时间和恢复引用；
  A `occurred_at`映射B现有`sampled_at`，不使用重试时刻。
- B当前DeviceEventCreate无reason字段，不能把额外字段默默忽略后称B已保存。
  原reason仍在A的持久事件/认证历史API，凭device/source可追溯；B保存同source。
  此映射和[正常／失败样例](../../../contracts/examples/a-device-event-delivery-rc2.json)
  是待三方签收候选，未擅改公共B DTO或冒充全S0冻结。
- B事务在A读事务关闭后运行，不持有A行锁跨接收；A只在匹配的
  `accepted_new/accepted_duplicate`、原source、预先固定摘要及目标UUID齐全后，
  重新行锁并比较原事件正文，再提交`delivered`。投递中原记录改变则blocked。
- B已经commit但A检查点失败：A仍pending，下一次沿原ID/正文重试；B去重后再标A。
  数据库故障返回retryable；invalid/conflict或坏回执为blocked，不标成功、不忙循环。
  未知接收器异常显式冒出，不吞成成功，不输出payload/凭据/原异常文本。

**本入口只尝试一次**。自动调度、持久退避/尝试次数、容量和操作人处理界面未实现，
不能把手工/验收调用说成常驻可靠通知worker。当前也没有对外通知或告警处置状态机。

## 本轮实际验证

1. 缓存Python3.14.8、完整后端605项/95%通过；新增21项Mock Session边界，
   服务语句99%覆盖。随后新增固定F04 JSON→实际B DTO映射测试，单独复测另记录，
   不把未重跑的605改写为606。完整app mypy58文件/Ty、范围Ruff与PS语法通过。
2. 真实内部认证Broker、独立PostgreSQL、真实B冷库HTTP创建与A绑定：
   新心跳→在线→真实停止发送→worker离线/持久事件→B实际接收入库；
   模拟A检查点提交故障时，B行已经存在而A仍pending；再试命中duplicate，
   B仍一行，随后A标delivered。重试原始正文不变。
3. 再发真实心跳→恢复，新source引用原offline；B暂不可用时A保持pending，
   恢复投递后B按来源HTTP回读正确关联，总计两行。原A reason未改，
   显式合成held_unbound夹具未投也未被重绑定。
4. 测试离线阈值3秒，生产默认90秒未改。B仅在严格临时库守卫之后按顺序
   测试专用DDL建Warehouse/Batch/AlertEvent三表，不是B Alembic/生产create_all。

原证据：`work/a01-tests-20261015232842-52708/pytest.xml`；
`work/device-events-20261015232827-56088/mqtt-device-events.json`，SHA256
`a747085885dc76bb3882c414f72d74d3976370802890d61ce16e321d913be813`。
证据只含合成环境，不公开真实配置/原始业务日志；同提交远端检查另行核验。

复测：项目目录运行
`powershell -NoProfile -File scripts/windows/check-device-events-mqtt.ps1`。
使用缓存镜像、独立tmpfs数据库/内部网络，无主机端口；清理只限本次唯一项目，
恢复本进程设置，宿主Python和原业务库未改。

## E1与下一步

本轮通过的是设备事件子链，不是通知送达。用户已回答两渠道“还没准备好／不确定”，
B仍需集中完成本人授权/配置、真实APP适配器、业务派发和两种终端实收。
C正式MQTT入库、历史/CSV/页面、全系统恢复处置、14设备/第二机未测。
B三项返修与三方同版签收继续按#5；正式迁移依次接已合并唯一head，不另起平行链。
