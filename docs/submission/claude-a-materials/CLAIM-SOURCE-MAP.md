# 关键陈述与来源对照（最多 15 条）

把材料里会出现的陈述逐条对应源 SHA/路径、验证层级和终验负责人。区分「原始数据/清洗」、
「同筛选 CSV」、「设备 ACK」、「异常恢复」、「服务接受与终端实收」、「候选第二机与最终
第二机」。这不是新的验收工具，不修改公共清单。

验证层级缩写：S=源码/单测，P=实际 PG/HTTP，M=实际 MQTT/ACK，N=合成派发（非真服务），
C2=候选第二机，X=未完成/待实测。

| # | 关键陈述 | 源 SHA / 路径 | 验证层级 | 终验负责人 |
|---|---|---|---|---|
| 1 | 原始数据与清洗数据分别持久保存，清洗不覆盖原始值 | `docs/modules/A/55-current-c-read-ports.md`（`ecf19d0`）；C storage | S/P | C 本人 + A |
| 2 | 同筛选 CSV 与页面/历史集合完整一致（ID 对拍，52/39 行） | `docs/modules/A/46-gated-fleet-storage-control.md`（`62bb2ef`）；A-TEST-REPORT-CANDIDATE A-C-10 | P/M | A + C |
| 3 | 十四逻辑设备真实心跳、七路遥测、执行 ACK 后降温 | `docs/modules/A/35-fourteen-device-runtime.md`（`8cd5f2dac57df94699b2e06e1c398320b5393eb5`） | M | A |
| 4 | 控制命令不重复执行（重放原 ACK 一致，execution_count=1） | 同上（A35，`8cd5f2d`） | M | A |
| 5 | 停发离线 + 同状态重启恢复，并关联原离线事件 | `docs/modules/A/46-gated-fleet-storage-control.md`（`62bb2ef`） | P/M | A |
| 6 | 设备执行 ACK 之后才有新温度数据（执行闭环定义） | A35（`8cd5f2d`）、A46（`62bb2ef`）、A44 `9c0a2956` | M | A |
| 7 | 本地准备已发布；候选停采样后真实HTTP恢复/回读/幂等已过，持续恢复与更正仍未完成 | main `36cdc449d6f6b8bdb50b8cd0e74f2bacf97b4546`；PR79 c2a9a67＋私有HTTP驱动，报告hash `1377b1d1fdd1089ea695c90f9670907026aff7541d4136bb048f4281d09fdb92` | S/P；持续完整投递仍X | A + B + C |
| 8 | 通知被服务接受（合成 SMTP/APP，attempt 0/2/3/3） | `docs/modules/A/54-notification-runner-composition.md`（`1b7a9d91e6a8a426eb4995e7f9114bff1a399ba7`） | N | A（标注合成） |
| 9 | SMTP 与飞书手机端两渠道终端实收 | 无——`complete_e1=false`、`notifications_sent=0` | X | B 本人 + 收件人 |
| 10 | 候选第二机已跑过候选 14 设备包（应用 `96afaab`+工具 `812eea`） | [C本人第二机原始记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6394870061)；不是最终同版签收 | C2 | C 本人 |
| 11 | 最终冻结版在第二电脑同版复现通过 | 无——仍未签收 | X | A + B + C |
| 12 | 唯一迁移链 A03→B01→A04→A05→C01 空库升级/check 通过 | `docs/submission/design/README.md`；`A-SYSTEM-DESIGN.md` §5 | P | A |
| 13 | 33 表、19 核心实体、17 条真实外键边（只读反射） | `docs/submission/design/schema-inventory.json`、`er-edges.json` | P | A（冻结后重反射） |
| 14 | 不同头回归分别记账；PR79候选1365条/0跳过/92%，不与旧1341/1362相加 | [PR79精确c2a9a67](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/79)，[#6实际记录](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6#issuecomment-6397957585) | S | A |
| 15 | 完整 E1 端到端全链通过 | 无——仍未签收 | X | A + B + C |

说明：验证层级为「已做过的验证类型」，不等于该项已被签收为最终通过；X 行只说明
「尚未证明」，不得在材料中改写为通过。不同 SHA 对应不同运行，不跨版本累加数字。
