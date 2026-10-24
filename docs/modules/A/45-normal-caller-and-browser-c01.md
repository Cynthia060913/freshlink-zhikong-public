# A45：常温调用与同版 C01 浏览器接线

2026-10-24；C 独立来源 `8eb66729fd03d765ff9cfa265b408dc399214743` 正常合并保留
历史，A 只修改本人 caller、测试和共享证明入口，不改 B/C 模块、活动迁移或真实配置。
发布基线为 PR57 合并 main `a1d185eea504f4d389a65c9431bf0e62d4827cf6`；以下均是
后续候选，不继承 PR57 的合并许可或批准。

## 常温输入 → 同事务终态

`GatedHighWorker` 保留现有名称以兼容显式调用者。先从可信设备/冷库选择唯一在样本
时刻有效的 B 批次，读取 B 历史规则并结束短会话；原文质量重算一致且有有效规则时，
高温继续 C 高温门禁，未超阈值则实际调用 C `stage_gated_within_rule_candidate`。
A 门禁、C risk/job 同事务提交；重放 not_due 不再增 revision，异常整体回滚。
F02 严格高温规则明确兼容 threshold_high 和已存 temperature_high 历史命名，
F03 异常仍 temperature_high；不接受其它规则种类。缺规则/坏原文/多批次仍保持待评估，
不以只算第一批代替多批次。常温样本评价不等于原异常满足恢复窗口。

业务核心 `4792ac32fd0e899670c5d1af945a8f912b87927b`：

- 10项实际私有 PG 针对性通过：原高温/重放、canonical F02、无规则后有效常温、
  常温失败回滚、坏证据、错epoch、有界扫描及多批不漏评守卫。
- 完整后端 **1216 testcase通过，0失败/错误/跳过，覆盖率92%**；实际空库升级C01及
  model check通过。XML `work/a01-tests-20261024000335-72808/pytest.xml`，SHA256
  `e92d32344a1a6004fd2d8a260d2e2b6945fb25ab4731c4a85098bdcbc8a86924`。
- 同一受控真实MQTT/PG/ASGI运行通过高温建告警→设备执行ACK→后续采样入库→
  明确 B 后继历史规则 v2（20°C）→新样本常温门禁/重放；原8°C高温告警仍open，
  不偷换成恢复证明。12接收/7观测，原文/CSV和旧snapshot字节不变。
  `work/gated-b47702cfa7a74e1293b75c5e5457f1b6/gated-telemetry.json`，SHA256
  `9349f29d500f7a018543e08c4b76cb06725a68c7c06a23da33b0b6bf783a4e24`。
  source_dirty=false、exit0/清理true，真实外发0。

针对性私有helper首次报告的 c_sha 字段沿用9f旧模板，实际代码守卫/被测提交/Git父链
均为C8eb；不以该错误字段称测试了旧C。helper已纠正，原报告保留，以上正式实链JSON
含正确C8eb，完整测试按精确479源码记录。不是声称三方本人电脑均运行。

## 浏览器不再使用旧 C 测试建表

共享浏览器入口删除 `c_metadata.create_all` 和无门禁 ingest lambda；强制实际C01
head及完整表集合，显式安全fresh-origin后使用 A gated receiver / GatedTelemetrySink /
gated delivery。缓存应用镜像可作为参数传入，无全局安装。前端bundle取自已核验274
归档镜像，与当前frontend树 `df8b22157d1ffeff7fda343ac428def001a54cb3` 一致。

干净 `a494c271dd6fe1a1387d4c0d5c9af40551b81090` 实际Chromium148/Playwright1.62.1：
页面准备/取消零POST、真实HTTP控制→设备ACK→刷新读回、普通用户403、B告警确认/
追加历史和**合成恢复**后迟到ACK409；持续MQTT→A门禁/C入库→真实遥测页面原文/
固定快照CSV全量ID对拍，匿名401。24条C观测、162次交付、失败0，设备执行1次。

私有证据 `work/control-browser-20261024001615-15308`：server.json SHA256
`164bcfd237ca8bcc7c8c891012a0c218929a54482c5b8065f791cdff87a49713`；browser.json
`3296ae719c02cbd0a325a1e700c583309d66cba677795ad7b9078b12421b745c`；
proof0/cleanup0/dirty_before=false。真实截图与CSV保留在该目录，不上传个人配置或数据库。
Ruff及mypy app97文件通过。没有在a494重报完整1216；其app/tests业务树仍与479一致。

**首轮失败仍保留**：43c89e5在 `control-browser-20261024001131-20960` 页面检查完成，
但后台worker失败导致总proof1/cleanup0，没有server通过凭证；早期未记录具体worker
状态，根因未确定，不能称已根治。追加固定错误类型/worker状态的私有诊断后，a494
完整一轮通过，诊断本身不是业务修复；后续须关注是否再现，不隐藏此不稳定记录。

## 明确剩余出口

浏览器中的恢复是合成B输入，不是C恢复窗口自动触发；常温v2不是原告警恢复。
多批次原子评估、恢复全写与发前冻结复核、SMTP/飞书经授权终端实收、十四设备完整
业务、第二机、M01—M05成品和实际参赛投递仍未完成。未自动启用恢复或外发。
24日距离27日冻结约3天，先关闭硬要求；B无暇时只按确认的小包交接补位。
