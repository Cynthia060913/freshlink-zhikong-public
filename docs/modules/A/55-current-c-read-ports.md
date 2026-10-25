# A55：当前 C 已上传只读端口兼容验证

2026-10-25；限定依赖 A54，不取代其原始证据。没有自动恢复或更正事件发送。

## 精确来源与保留范围

C 来源 `09823df9b7ab5c884813aa46fa7dc1a05bc3cc7f`（PR11），仅接入：

- `telemetry/http_candidate.py`：认证历史/CSV/detail 禁止缓存，接收明细按实际 UTC
  时刻而非不等精度字符串排序，原始值保留。
- `telemetry/recovery_freshness_candidate.py`：A 设备门内只读核对 frozen frontier、
  原异常/恢复 body 摘要与身份、历史规则和窗口、迟到坏接收证据。ready_at_check
  只在检查时有效，不能证明释放锁后仍新鲜，也不是发送许可。
- 两个对应 C HTTP/严格 PG 测试文件；不整支合并。特别不复制 C 分支里较旧的
  `test_gated_high_business.py`，保留 A 已实现的全批次原子评价及其回归。

整棵 telemetry 源码及非活动 C01 迁移提案与上述 C 提交一致后才更新门禁 pin；
活动唯一 C01 内容不变，没有新建或并行迁移。A/B 生命周期与运行器不变。

## 验证出口与未完成

2026-10-25 15:41（北京时间），精确代码提交
`ecf19d092075fa825e565d7f5f47c39519432ead`、工作区干净，实际完成：

- **63实际 testcase 全部通过、零跳过**：C严格PG5、C HTTP8、A全批次13、
  A/C门内存储15、A生命周期并发13、A通知启动9。唯一链空库upgrade/check通过。
  XML `work/notify-subset-50ecf4673dc04e3ea440ab3b2233d74e/current-c-subset.xml`，
  SHA256 `cf50e4361a571d06895f517034308d914c1748d2ecb6a531a05c8531b67e1b68`。
- 当前C组合真实MQTT→四CLI→PG/TCP B→raw/同快照CSV→独立合成派发重启验证通过。
  `work/gated-7387543ca2134cfc8aa4ece023491f1c/notification-composition.json`，
  SHA256 `4d1199f5dd81608b4faefe360aaf5b86f2e5ec5b5123eb75bbd03f8b6a7af743`。
  合成attempt仍为0/2/3/3，`notifications_sent=0`、`complete_e1=false`。
- 新C两端口mypy与五个涉及Python文件Ruff通过；有限公开文本检查735文件无检出，
  不是完整安全审计。两专属项目清理回读容器/网络均为空。

A54的1341/92%属于先前代码提交1b7a9d9；本次C增量只补相关专项与实际运行，
不将旧全套自动套到新头，也不将二者相加称全套测试数。后续文档更新不改变已测代码。

C 严格恢复场景中的 original delivered 标记是明确夹具；它验证真实 B 历史规则与
真实 A/C PG 事务，不是恢复候选经 B HTTP 接收或通知实收。旧 F03 仍不支持
关联 data_quality 更正；双方契约及更正投递未签收前，继续不启用自动恢复。

后续需 C 对精确头独立审查；B/C 更正契约、恢复调度与真实 HTTP、两渠道授权
实收、最终同版第二机/浏览器和成品材料均另行验收。
