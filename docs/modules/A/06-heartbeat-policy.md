# 心跳边界与状态转换（A，待持久接线）

这轮实现 `heartbeat.py` 的纯接收校验和不可变状态转换计划，不新增数据库迁移、MQTT主程序监听器或B告警调用。它是后续服务可复用的生产逻辑组件；返回plan不代表保存成功，更不能把设备列表现在的unknown改成在线。

## 已实现的规则

- 可信adapter先完成凭据/ACL核查并加载注册身份；topic、设备自报UUID不能充当认证。注册但尚未绑定的设备可以发心跳，事件仓库为空时仍须保存，B如何处理另按F04确认。
- 最多32768字节、32层JSON；拒重复成员、无效UTF-8、非有限数字、未知schema、错误身份和保留消息。必须给出message_id与带时区sent_at，接收时间由服务器提供。
- 首版工程默认：90秒离线阈值、发送年龄达到90秒拒绝、最多30秒未来偏差。配置有效性检查；这不是赛事规定或实测性能指标。设备持续运行器默认每30秒发心跳。
- 用可信received_at更新last_seen，不用sent_at替代。未见过心跳保持unknown。重复消息、同ID不同内容、乱序发送时间不续在线；软件仿真时钟倒退需排查，不把旧消息恢复成在线。
- elapsed达到阈值产生offline，occurred_at是阈值时刻，不是任意扫描时刻。事件ID基于设备、上一心跳ID与阈值确定，同状态重复计算/扫描重启仍得相同ID。
- 新鲜心跳恢复时产生新的recovered ID，related_source_event_id引用原offline。扫描器漏跑但下一心跳跨过离线阈值时，一次plan包含offline/recovered两事件，不能悄悄抹掉中间失联。
- Connectivity仅描述链路；online不意味着温湿度正常、质量有效或风险低。

## 必须遵守的持久adapter顺序（未实现）

1. 校验消息；可信服务器身份读取不得信任包内仓库。
2. 开事务并锁设备状态；查询持久唯一键(device_id,message_id)对应fingerprint。不得把进程内字典当持久去重。
3. 调用plan；原子保存状态、消息去重记录和事件outbox。冲突证据独立保存，不覆盖首次指纹；错误不能返回持久成功。
4. 提交后才允许对外展示last_seen/status；B事件交付从outbox重试，accepted_new不表示通知实收。
5. 扫描器同样锁状态并原子保存offline/outbox；并发scanner与heartbeat必须串行到同设备锁，多个进程不能各自认为首次离线。

主程序/数据库适配、唯一约束、事件outbox、重启留存、B接收以及页面仍待实现和真实验收。状态与事件dataclass没有取代正式持久模型；共享迁移顺序须在S0确认后衔接A01→B→C。

2026-10-13下一小交付新增`registry.py::receive_registered_heartbeat`：从当前设备注册重读身份/仓库，凭据失效、超大包、保留消息在访问DB前拒绝；注册但未绑定允许心跳，包内仓库仍不能伪造或覆盖。DB故障向上抛出，不当作成功。**只读，不更新last_seen/状态，不提交plan，不是MQTT认证监听器**。隔离PostgreSQL验证8个新增用例（含参数化），完整后端206通过、95%覆盖率；严格规范/类型通过。此后续提交与已合入PR17分开记录。

## 检查

`backend/tests/api/routes/test_heartbeat.py`覆盖正常/不绑定、身份失配、JSON限额、过期与未来边界、重复/冲突、乱序、首次unknown、精确离线、重复扫描、恢复关联、延迟扫描、配置与非法状态。实际测试结果由本轮GitHub状态和证据文件记录，不把单元测试当作E1。

后续出口：隔离PostgreSQL下的事务幂等/并发/重启→真实MQTT服务端心跳→API在线状态→B离线/恢复投递与实收→E1。不得仅修改前端文字或靠浏览器定时器伪造在线。

## 同轮A/C真实源码边界交叉诊断

脚本`backend/scripts/check-a-c-boundary.py`实际导入A的receive_telemetry与C的prepare_receptions，9项通过；确定版本和未覆盖范围见`evidence/a-c-boundary-20261012.json`。单独A/C目录只读挂载，容器禁网；未复制、修改或接管C源码。这不是主程序入库联调。

C源码尚未合入A分支时，需先取得该确定C提交，在隔离容器把其`backend/app/modules`挂至`/tmp/cmodules`，设置`FRESHLINK_C_MODULE_ROOT=/tmp/cmodules`、`PYTHONPATH=/app/backend`，再执行`python scripts/check-a-c-boundary.py`。A backend挂至`/app/backend`只读。最终三模块合入后不需额外C路径；缺C代码时明确import失败，不能跳过并伪报通过。参数只接受本地可信审查源码路径，不用于下载外部包。
