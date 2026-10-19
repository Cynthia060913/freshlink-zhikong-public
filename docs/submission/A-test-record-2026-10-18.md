# A 独立测试摘编：2026-10-18（M03 底稿，未签收）

本表记录精确来源与运行边界，不能把不同分支的数量相加当作完成率。
同日旧失败保留，只有明确新头的复测结果覆盖相应缺陷，不修改历史原报告。

## 1. 环境和证据原则

Windows 上只使用已有缓存 Docker：CPython 3.14.8、PostgreSQL 18.6、锁 digest
的认证 Mosquitto。临时项目使用独立网络、无宿主端口，PG 使用 tmpfs；原应用库、
真实配置和已恢复的宿主 Python 未修改。真实邮件/APP/短信没有发送。
本机私有 work 保留原报告；公开只提供脱敏结论及 SHA256，不附真实配置/原数据库。

## 2. 实际运行表

| 对象/准确提交 | 预期与操作 | 实际结果 | 明确未覆盖 |
|---|---|---|---|
| main `095c3a7b34ceacd4b9d52cbea180d77de596f2dc` | 专属 PG 升到 a03，真实 Uvicorn 子进程访问 health/登录/设备 API | health200、匿名401、密码登录和管理员回读；登记201且初始unknown，重复409且总数仍1，列表/详情通过 | 不是浏览器操作；不是 B/C 本人环境或完整 E1 |
| A18 `ed48ddbfec7a2de7440ab3a6472484062a94cf24` | 完整后端 + 实际认证 Broker 控制/ACK | 340通过、96%；worker语句覆盖100%；重放仅执行一次、伪ACK拒绝、worker重建续原包、timeout/late不改成功 | B lookup 是显式测试夹具；未部署正式worker或接C |
| B `7b1cf26d9ad1e7ec52b41654550ad2615bc097f9` | 独立复核三项返修并完整后端 | 443通过、94%，其中alerts245；暂时批次读故障传播、后继永久闭窗、severity三值修复通过 | 不是恢复状态机或两渠道实收 |
| C `a97b9c04ff9cb3cd32872e87335b11bfadf677e1` | 严格 test-db 下整文件三项 PG opt-in | 3通过/0失败/0跳过：真实JWT/HTTP/raw/固定CSV/跨人/503；幂等回滚并发与新连接；晚提交只进新快照 | 正式C迁移、主程序挂根/页面和实际C→B未测 |
| A/B运行 `52ed0ea9fe9e7534e8005ff0d2a8f9b392e725ee`（证据文档提交523f5fa） | 使用上述B修复，统一根路径和真实仓库依赖 | 603通过、95%；根接线18项；B创建冷库→A单次绑定→真实lookup→MQTT控制/ACK通过；生成32个API路径且/api/v1只一次 | B DDL仅测试；未正式迁移/部署、未C/两通知/E1 |
| A后续栈 `0d115bb20f186bd9b1e176e8ab846e7c4701d9e7` | 同步上述B修复后整体后端、真实自动事件投递 | 727通过、95%；真实心跳离线/恢复→A持久事件→B新事件2条；暂时故障有界重试、恢复等原事件checkpoint、新worker/池尝试预算1→2通过 | 正式A10/B迁移、OS进程重启、通知实收、完整E1；固定C来源仍是该栈早期候选，不是最新C整合 |
| B恢复新头 `9cf9a5de55d48bf0761655b6f2cda844ec5975f6` | 完整后端 + 固定摘要/旧行重放/原规则恢复独立探针 | 后端462通过、94%；但跨边界探针两项失败，PR10 review5563581508，当前不建议合并 | 不能用单测全过取消实际缺陷；未将此头放入上述A版本 |

## 3. 缺陷与复测

- C66b 的三项最初为 2过/1失败：class 共用 schema，HTTP 夹具使质量计数串扰。
  C在b8692b5改每测试独立 schema，原断言未放宽。A对最新a97整文件3/3通过；
  独立查询 c_test_% 剩余0，专属PG/网络清理exit0。不能把旧失败说成业务重复写入。
- B9cf 给普通候选增加 related_source_event_id=null 后，原 B7b/Ca97 固定摘要
  20789a39… 变为 f862dc32…；旧同ID同正文实际返回 conflict。恢复使用不同规则
  snapshot/version 和 severity，仍能 accepted_new 并把原告警 resolved。均已用
  准确B源码及合成内存库复现，交B修，未直接修改其源码或重写旧事件。
- A10 早先云端报告写入失败为宿主无法覆盖容器root拥有的报告；已改成保留原报告、
  CreateNew 来源旁证，不 chmod/重写原报告。0d实链旁证记录干净完整source SHA。
- pytest终端与Junit testcase实际节点按本表计数；例如0d为727个case。其原XML
  suite总计属性为810，未擅改原报告，也不采用该属性夸大用例数。

## 4. 原证据校验值

| 报告 | SHA256 |
|---|---|
| main真实进程/API结果 | 49dc54c41fbbebf507acfb18ce01d5962dbe37df56dd751a8388ff64fb639655 |
| A18后端XML | d821378e12ad9720b90f9e512077f64b2a6c6d3586dbfe9f6b0c9a057d28133c |
| A18实际MQTT控制 | 3e368605476f45113309ada14a297fb17efecc3f172c60e287da767c20399f0b |
| C a97三项PG原日志 | edb990bf3b0ed88da36e1e194eaab429d7119a73b233e2aa0cf29ef1661aa2fc |
| C a97测试/清理结果 | 21677843bfed694f80c242a46136e939183e4cf21d5d539fc12c16acc6c5118e |
| A/B 52ed后端XML | 3a9508d396ca91969db0ab4da1ed113ec9162c7b7bd6dae7fc751cd304e5fe57 |
| A/B真实仓库绑定/控制 | 1ee0ff92498c285026b936293f1208bce0487c83a2bf6162d5b13be24510d2a6 |
| A后续0d后端XML | f3bb2b9d97e71058fc3600f6fa422d3adf00ddde209add143ed0c4631ade1aff |
| A后续0d自动事件原报告 | 226867fd6226d0d084719a758831da4b648a74f0fa72b6158574cee1d67c0522 |
| A后续0d自动事件来源旁证 | 3c338a341cc69f0e9863572571adf9196d61a0c353cb15233df5a4ee67dfe097 |
| B9cf跨边界探针原日志 | 03fe428d293ac9446880f1c4cf4465425f60691ab22a04d350684875894d8984 |
| B9cf后端XML（通过不覆盖探针失败） | 2bedb137dab318cb357ee16f9ee75516f8fccbb9a132595326a0f3cf6590ace4 |

## 5. 复现与下一验收

准确候选目录下可用受控脚本 check-a01-acceptance.ps1、check-control-mqtt.ps1
（真实B绑定候选使用-WithWarehouses）、check-device-events-mqtt.ps1。仅使用已缓存
镜像/私有临时结果；C三项用A24精确 test-db/FreshLink-test 入口，普通CI跳过不算通过。
各脚本启动前应核对来源SHA与未提交成果，不在测试期间切换其挂载的源码。

下一验收为：同头评审与门禁、正式唯一B/C迁移、主程序运行入口/页面、C→B和原规则
恢复、两独立通知分别实收、同一库完整E1、两库三批14设备/第二机/完整运行包。
最终材料对齐同一发布SHA，依次补真实截图/测试/视频，不把这些局部结果当最终成品。
