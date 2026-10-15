# A 测试记录摘编：2026-10-15（M03底稿，未签收）

维护者A；源码[53833a7921fbf700a3f945de449646cdc3d88b2d / PR18](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18)。
本记录只签其列出的测试事实，不代替B/C、E1和最终发布验收。C可汇编到团队测试报告。

## 环境和证据

- 本机Windows、Docker隔离Linux、CPython3.14.8；PostgreSQL18临时tmpfs，
  Mosquitto镜像固定digest，内部网络不开放主机端口，无外部通知。
- 原业务数据库、系统Python和真实配置不参与测试。B仓库为显式合成lookup。
- 完整后端：`work/a01-tests-20261015170849-42484/pytest.xml`及coverage报告。
- 真实网络证据：`work/control-mqtt-20261015171105-37592/mqtt-control.json`，
  SHA256 `626139e3d7ddef121b0c3f5f104f410f8e1b4d8cacb65fbef3191c394201aa7f`。
  详细原报告保留本机私有work，不把配置/数据库/未脱敏日志公开。
- 可重复入口：`scripts/windows/check-a01-acceptance.ps1`和`check-control-mqtt.ps1`。
  需要已缓存对应镜像；不依赖宿主Python安装。测试清理仅本次唯一容器/网络。

## 测试事实表

| 编号 | 输入/步骤与预期 | 实际结果 |
|---|---|---|
| A-T01 | 空临时PG升级A01→A02→A03，执行完整backend pytest；失败数0、覆盖≥90% | 340 passed，0 failure/error，覆盖96%；其中新增44个控制worker适配器用例 |
| A-T02 | 默认TLS/证书检查、受控主题、拒绝降级订阅、ACK提交先于PUBACK、DB故障不确认 | 单元回归通过；worker语句覆盖100%，不是完整并发/安全证明 |
| A-T03 | 真实心跳上线后管理员创建制冷命令，经Broker由仿真器执行，回执可由API查询 | 状态acknowledged，传输接受和设备执行时间分别保留；后续MQTT温度样本下降 |
| A-T04 | 同HTTP意图和真实Broker重放数据库原包；预期不重复执行/不增第二条相同ACK | 第一条合法命令执行次数仍1，回执只有1条 |
| A-T05 | controller服务账号向设备ACK主题发包；预期Broker拒绝 | 收到拒绝，未伪造执行成功 |
| A-T06 | 设备停止；第二条命令已获Broker接受后实际停止/重建服务器worker，再重启设备 | 从数据库投递相同payload，第二条合法命令执行并ACK，总执行次数2 |
| A-T07 | 设备再次停机；2秒TTL命令获Broker接受但无设备执行；预期timeout | 保存timeout/原终止时刻，不将sent作为成功 |
| A-T08 | 重启设备后重放过期原包；预期设备拒绝、late证据不改终态 | 实际expired ACK保存为late，命令仍timeout，总执行次数仍2 |
| A-T09 | Ruff、全app mypy、Ty；暂存文本与安全自测 | 静态/类型通过；431文件文本扫描零发现（9二进制未按文本扫描），安全脚本10/10 |

当次测试中B仓库不存在真实表，使用固定UUID并明确注入测试lookup。
遥测仅从实际MQTT订阅观察，**不是C数据库入库或清洗验收**。没有给邮箱/手机发送告警。
模拟温度变化只验证软件状态影响后续采样，不外推真实温控效果、品质概率或节能率。

## 远端检查及失败修复

[PR18检查页](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18/checks)
已回读同HEAD后端、Compose、pre-commit（含类型）、Zizmor、Playwright四分片及聚合全部成功。
这是当前远端检查结论；后端原始日志下载因网络EOF未独立提取计数，不能把本机340复制成
已读到的云端计数。报告合并按默认开关跳过，不构成业务测试。现有浏览器集不含新控制页面。

首轮完整本机回归339过/1失败：新测试误用Paho SubscribeOptions的`qos`属性；
实际属性为`QoS`。修正测试、完整重跑340过；未跳过用例或降低覆盖门禁。
剩余两个警告为现有TestClient依赖弃用、后端只读挂载没有前端构建目录；不据此宣称网页
失败或网页通过。真实控制网络测试随后增加了服务器worker停止/重建场景并独立复测通过。

## 未测清单与下一签收

生产TLS证书和ACL安装、Broker断线/重启故障注入及长时压力，B真实仓库和事件/告警接收，
C真实观测持久化和历史CSV，控制UI及专项浏览器，两个独立通知渠道实收，E1、十四设备、
第二机和最终压缩包均未在本记录中验收。交叉审查与按依赖合并也不由CI自动替代。

这些缺口仍留在新库#5/#6/#7/#8/#9/#12，后续用同版真实结果补齐，不关闭整个M03或项目任务。
