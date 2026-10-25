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

先在专属临时 PG 验证 C 五项严格数据库场景、HTTP/原始接收排序、A 的全批次和
生命周期防护，再实跑当前同版 MQTT/四CLI/TCP B/历史CSV/合成持久通知。
本说明在运行前只定义验证范围，不预填通过。

C 严格恢复场景中的 original delivered 标记是明确夹具；它验证真实 B 历史规则与
真实 A/C PG 事务，不是恢复候选经 B HTTP 接收或通知实收。旧 F03 仍不支持
关联 data_quality 更正；双方契约及更正投递未签收前，继续不启用自动恢复。

后续需 C 对精确头独立审查；B/C 更正契约、恢复调度与真实 HTTP、两渠道授权
实收、最终同版第二机/浏览器和成品材料均另行验收。
