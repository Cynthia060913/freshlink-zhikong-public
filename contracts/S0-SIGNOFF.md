# S0 统一签收候选：0.2.0-rc3

2026-10-17。状态：**PROPOSED，尚未三方签收，不关闭 S0**。

本文件和 `s0-signoff-manifest.json` 构成一个不可变签收包。签收时写本包的
**完整 Git 提交号**；不是只写 rc3、分支名或“接受最新版本”。manifest 把六类
接口的来源固定到公开库完整 SHA，故其来源分支后续变化不会悄悄改变本包。
此包汇总规范目标，不导入 B/C 业务代码，不表示这些业务已合并或实测完成。

## 1. 事实、优先级与最小出口

- 共同基础 `637574acdebdfa0d2041b49e5f974b6286f6e7fe`：PR21 的截止/阶段文档经
  C 同头审查和适用检查后已合并。目标是 **2026-10-30 及之前实际投递**，
  争取 25—28 日完成成品复核和投递，不保留 31 日开发缓冲。
- A13 `d57e9dc9ed85f74f6c32141320d9df11db1bc84f` 仍待有效独立审查；A03
  不是当前共同 main 已发布的正式迁移 head。按实际合入的唯一链 A→B→C
  接续；不存在的正式迁移不能靠测试 `create_all` 代替。
- 本文件明确的新目标优先于 manifest 来源中的旧草案。来源中的“已实现”
  只证明其候选范围，不覆盖本文件列出的未实现项。未列新目标沿固定来源。
- S0 只要求共同基线、字段/错误/幂等/正常失败样例、边界和本人接入记录，
  三方同版接受并经审查发布/取得。**不要求全部 B/C、两通知或整个 E1 先完成**。
  但不能用“契约接受”放行仍有实际缺陷的业务 PR。

## 2. 六类接口的同版目标

| ID | 固定签名/关键内容 | 正常与失败样例/验收目标 |
|---|---|---|
| F01 A→C | 可信封装 `raw_text/raw_payload/authenticated_device_id/warehouse_id/received_at/reception_id`；设备/仓库由 A 核验，接收时间来自服务器；同一次调用重试保留 reception_id，网络重传可新增；C 返回持久 observation_id | manifest 的 A fixture 与 C telemetry fixture。UTF-8 32768 字节、32 层、16 metrics、key/unit 64 字符；非法结构/身份拒绝，合法 JSON 的坏采样时间/单位保留 raw 为 unknown；重复/冲突不覆盖原文。扩展字段按已确认等价规则参与内容身份 |
| F02 B→C | `select_rules_for_observation(session, *, warehouse_id, batch_ids, metric_code, sampled_at) -> RuleSelection`；统一 status/rules/reason_code，不混用 list/dict；按 UTC 半开时段、入出库和批次分别选历史规则 | configured 返回各批次独立规则；no_rule 返回空 rules，C 判 unknown；永久参数/未知对象 invalid；暂时数据库异常沿 SQLAlchemyError 传播给 C，C 留任务重试，HTTP 边界转 503 + Retry-After:30；不把读故障变 unknown_batch |
| F03 C→B | `receive_candidate(session, CandidateCreate) -> CandidateReceipt`；单批单指标；稳定 source_event_id；schema_version=1.0、observation_ids、规则/算法身份、sampled_at、evidence 均持久并入摘要；severity 固定 info/warning/critical | manifest 的 RC2 正常样例及 §3 数据恢复增量。首次/重复/改正文/永久拒绝/临时失败分别 accepted_new/accepted_duplicate/conflict/invalid/retryable_failure；HTTP 201/200/409/422/503，503 含 Retry-After:30；超时后 by-source 未找到仍用原 ID/正文有界重试；接受不等于通知 |
| F04 A→B | `receive_device_event(session, DeviceEventCreate) -> CandidateReceipt`；source/device 保持原 UUID 文本；A occurred_at 映射 B sampled_at；未绑定 held_unbound 不发；恢复另取新 ID 并引用原离线 ID | manifest 的 A delivery fixture 覆盖原事件/恢复、未绑定、临时失败、B 已提交但 A checkpoint 丢失后的 duplicate。原 reason 当前留 A 历史，以 source/device 追溯，不假称 B DTO 已存 reason；B 若需直接保存 reason 先提兼容增量 |
| F05 A 控制/B 引用 | POST `/api/v1/devices/{device_id}/commands` 管理员写，GET 同路径及 `/{command_id}` 活跃用户读；command_id 稳定，正文变更冲突；B 只读引用 A 的 command_id/事件关联 | manifest 的 A control fixture。queued/sent 不是执行成功；只有可信设备 ACK 可 acknowledged；超时后晚 ACK 留证但不翻转成功。未知 B warehouse 查询 fail-closed；不能用恒 True 代替真实 lookup |
| F06 C→页面/CSV | `query_observations(actor, filters, limit=20, cursor=None)`；`export_observations_csv(actor, filters, received_watermark, *, snapshot_id)`；列表/详情/CSV 路径沿 A §7，外层统一 `/api/v1` | manifest 的 C 非空快照及 A 并发向量。固定有序成员与投影，不只靠时间水位；同 snapshot 的页/total/CSV 一致；晚提交旧记录只进新快照。TTL900秒、最多10000条、每 actor4/全局64；容量429 snapshot_capacity + Retry-After:30，不驱逐有效快照；失效/跨人/条件不符422，存储故障503 |

F02 的具体 RuleSelection 条目/DTO 使用 manifest 中的 B 代码形状；本包规定
**读写采用同一 supersedes 闭窗计算**：收集所有后继链接，前驱在最早后继开始
时刻永久关闭，而不是只收集“当前仍有效”的后继。合成边界：S1=[10/1,+∞)，
S2=[10/2,10/3) supersedes S1；10/4 应无规则，不能复活 S1。S3 新版另行合法
接续，不改原规则内容/既有告警引用。原 B 两项 F02 缺陷与 severity 收窄仍须修复。

所有 HTTP 首版沿现有认证：未带凭据401、无效令牌403、禁用用户400；管理员
写、活跃用户读，没有逐仓 ACL 则在界面/材料如实说明。每次查询、计数、详情、
CSV 都复核权限；签名游标不替代授权。模块 router 不重复添加 `/api/v1`。

## 3. 数据异常恢复：A 的明确建议，待 B/C 接受或给替代

已读取 C 固定恢复 JSON，不再泛泛要求 C 重交同一份样例。下面裁决三项待定
语义，均为**新契约目标**，不表示已有恢复算法/B 状态机。

1. 以**原异常引用的不可变规则快照**判断该异常是否恢复，保留原 severity、
   rule_snapshot_id/rule_version。新规则用于新的异常评估，不回写旧异常。
   原规则缺少明确恢复阈值/比较符时不猜，保持异常或 unknown，交配置/人工处理。
2. C 先生成并持久保存恢复判据与 `algorithm_version`：最小策略为同设备/仓/批/
   指标，原异常之后 **3 条不同且质量 ok 的有效新样本**连续满足原恢复比较符；
   相邻采样间隔上限工程建议 **90 秒**，须写入 evidence 与版本，不是农艺结论。
   规则若明确更严格的样本数/时间条件，按规则，不能悄悄用固定值覆盖。
   缺测、坏时间/单位/质量、超过最大间隔或再次异常重置累计；重复不重复计数，
   冲突留证且重置。迟到/乱序旧样本保留历史，但不用于自动恢复计数或回改已确认
   关闭状态；按接收流只推进严格递增的 sampled_at，迟到事件单列待评估证据。
   未声明乱序处理/缺测判据时不得生成“已恢复”。C 若认为此最小工程策略不合适，
   需给一个具体可实现替代及正常/失败 JSON，不接受无终点讨论。
3. C 新恢复 ID、`kind=temperature_recovered`、`related_source_event_id=原异常ID`；
   其余 F03 追溯字段/摘要/回读不减。先确认原异常已被 B 接受，再投恢复；未确认
   则保持 pending。B 验证原事件、仓/批/指标关联，将新的恢复记录及原告警
   open/acknowledged→resolved 在同一事务提交；保留原异常和操作审计，不删历史，
   不等于人工 closed。同 ID 同正文重放不重复关闭/派发；改正文 conflict。
   未知原事件不制造正常或关闭，给可追溯的显式拒绝/等待原因；C 保留失败证据。

C JSON 中的 8°C 与 3 条回落数据仅是**合成例**，不是默认苹果农艺标准。
B 当前 CandidateKind/DTO 尚无 temperature_recovered/related_source_event_id，
需 B 自有兼容增量、摘要和真实回读/重复/冲突测试；未知字段不能静默丢失。

## 4. 角色边界、签收和下一步

- A：身份/MQTT/接收队列/心跳/控制、共享根/导航/Compose/契约汇总；B：仓批规则/
  告警状态/两通知/本人 UI；C：原始/质量/快照/CSV/候选 outbox/本人 UI。
- B/C 不必等 S0 关闭才修本人问题、上传独立候选/页面/材料；正式迁移只接实际
  已合入唯一 head。共享改动先写 integration-notes，由 A 协调，不同时抢改。
- 新旧仓库不混用；只向 freshlink-zhikong-public 当前任务/PR 写入。旧历史、
  真实配置、日志/数据库和私人地址不迁入；不共享 A 的凭据。
- 真实通知渠道与收件授权尚未齐，不发送，不以 SMTP 接受/Mock/Disabled 算实收。

请 B @xixidamei、C @im1yt 在 **#5 同一回复**逐项填写：

```text
角色 / 实际共同 main 完整 SHA / 本人分支完整 HEAD：
签收包完整 SHA（不是分支名）：
F01/F02/F03/F04/F05/F06：各 ACCEPT 或 NEED_CHANGE：
§3 恢复三个决议：分别 ACCEPT 或具体替代及 JSON/测试路径：
文件边界、根前缀一次、唯一 A→B→C 迁移链：
本人基线取得/实际启动/登录/设备页检查：结果/证据/明确未覆盖项：
已实现和未实现项分开；下一小交付/可独立工作：
```

A 同样在 #5 对**生成后的实际完整 SHA**确认。三方接受前不改 PROPOSED，
存在字段异议则形成新包后仅重签相关差异，不要求从头重做全部协议。全部真实
满足后经独立审查、同 HEAD 适用检查合入，再公布共同 main SHA 和三方取得记录，
关闭 S0；E1、角色主任务和材料仍按实际结果分别验收。
