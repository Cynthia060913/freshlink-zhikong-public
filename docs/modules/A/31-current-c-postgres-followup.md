# A31：C 最新第六项 PG 与全应用类型门禁补验

2026-10-21 19时，北京时间。优先处理 C #5 评论6307881311的实际请求，不重复
A40 已跑过的完整1058项。源为 C **`bf991a0c610dd91cc89be950af15ae020f711bd5`**，
相对 f895 仅新增PG恢复专项/两份材料，以及 outbox SQLSTATE 的一行 str 收窄。

A 当前候选正常 merge C 到 **`6148800a5439e1099dc04fd22e1cfaeca70294ce`**，
C业务与PG测试子树逐范围diff=0，不代改C源码，不改变正式迁移或外部通知设置。
下层 A30 的旧 f895/全mypy未过为历史，本轮以以下实际结果补充。

## 实际验证

- Python3.14.8、PostgreSQL18.6，受控 READ COMMITTED，严格开启 C PG 守卫。
  `test_telemetry_storage_postgres_candidate.py` **6/6通过，0失败/错误/跳过**，0.97秒。
  新第六项实际执行：调用方同一Connection标原事件delivered、冻结/重复恢复outbox，
  外层回滚后新连接只见原始pending；再次提交及重复只保留一条恢复候选。
- 这项原事件delivered是测试前置写入，不是实际B已送达或两通知实收。
- 测试后独立查询 `information_schema.schemata`，`c_test_%` 剩余0；test_exit=0、
  cleanup_exit=0，本轮临时PG/网络已清理，原应用DB和宿主Python未修改。
- 同缓存依赖镜像全应用 **mypy：93 source files无问题；ty check app通过**。
  因此旧 C outbox no-any-return 静态阻碍在此组合中已解除，不再让 C 重复修。

证据：`work/c-pg-followup-20261021190601-17868/c-postgres.xml`，SHA256
`6a4cdeed5079267b707cb7e496fb5790bf35364d5288cbe36d5ee27feb0f7fdf`。
六项受控PG不等于 B/C正式发布、A24全部写门禁或自动恢复。

## 下一步

PR42精确6ed501f的 C APPROVE review5594935429已回读，且该PR当前头未变；该批准
不延伸到 PR43 新增A24字段、C最新增量或 B 新飞书049f1c5。
PR43供C按精确新头审前沿/同连接边界；C可直接继续其自有恢复写入口、冻结关联和
发前复核，A协调唯一迁移与共同运行。B继续通知三项返修/派发/授权配置和确认实收。
27日前整套必选可跑且材料可提交，算法和装饰不新增。
