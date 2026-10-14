# A1.2 注册与绑定适配器（待正式接线）

实现：`backend/app/modules/iot/registry.py`。基于 PR #14 的接收边界与已发布 A01 `iot_device`；不新增表或迁移，不改 B/C 源码。共同契约仍待明确确认。本文件描述已实现的内部函数，未开放绑定 API 或生产 MQTT 入口。

## 已实现行为

`bind_registered_device(session, actor, device_id, warehouse_id, warehouse_exists)`：

- actor 由未来根认证的 CurrentUser 提供，必须活跃且为管理员；普通/禁用用户拒绝。
- 从真实设备表取记录并锁行；用 B 的可信仓库服务核验目标存在后，写已有 warehouse_id 字段。缺设备或仓库拒绝。
- 同一绑定重试保持结果；已有绑定指向另一冷库报 binding_conflict，当前不支持静默迁库/解绑。历史观测、在途控制与候选的迁库政策明确后再扩展。
- 两个并发首次绑定通过 PostgreSQL 行锁序列化；失败 commit 回滚。调用方使用请求独占 session，并在失败后回滚/关闭。

`receive_registered_telemetry(session, authenticated_device_id, credential_enabled, warehouse_exists, topic, payload, received_at)`：

- 上游可信 MQTT 适配器负责凭据和 ACL 校验，提供已认证 UUID、当前凭据启用状态以及服务器接收时间；不能用载荷里的 device_id 充当认证。
- 超限和停用凭据提前拒绝。从数据库重新读取当前设备，避免长 session 缓存旧绑定；未注册、未绑定或仓库无效拒绝。
- 将真实设备/绑定交 receive_telemetry，载荷仍须通过身份、主题和 JSON 边界。原文不补写仓库 ID，不更新 last_seen_at，也不把封装成功当 C 已持久化。
- 数据库/仓库服务不可用异常原样交上游处理，不能伪装成正常结果或永久坏数据；上游可靠队列与重试仍待实现。

## B/C 接线需要的输入

B：提供在当前事务/请求中读取真实仓库的服务。当前适配器接受 `warehouse_exists(UUID) -> bool`：存在返回 True、不存在返回 False，数据库故障抛异常。可在集成时包装 B 的 `get_warehouse(session, warehouse_id)`，只把 WarehouseNotFound 映射为 False，其他异常必须保留。B 尚未合入的服务不能直接在主应用 import。是否允许停用/删除已绑定仓库，须在 S0 明确并补测试。

C：接收封装仍沿 A1.1。缺原始 warehouse_id 时用独立可信封装校验，保留 exact raw；已有原包的显式身份与可信绑定不一致拒绝。C 负责真实观测事务、去重、reception、质量与候选；本模块没有调用 C 或写其表。

最终对外绑定路由由 A 在 B 正式迁移/服务通过审查后挂，复用管理员认证，核对 OpenAPI 和实际 HTTP；A01 当前创建时的未绑定限制仍保留。此内部实现不单方冻结三方接口。

## 本轮实际测试

2026-10-12 使用现有 Python 3.14 镜像和一次性 PostgreSQL：完整后端 **108 passed**、总行覆盖率 **93%**；registry.py 行覆盖率 **100%**。新增 **17 个执行案例**覆盖：真实设备查询、绑定后新 session 重读、权限、缺对象、重复/不同绑定、两个并发请求、失败回滚、仓库服务故障、旧 session 缓存刷新、载荷伪造身份/仓库/主题，以及提前拒绝。

证据本机私有目录：`work/a01-tests-20261012091337-60712/`。临时测试数据库与网络已清理，运行应用数据库未触碰。完整 app 的 mypy/ty 与定向 Ruff 通过。

仓库有效性在这些数据库测试中为明确替身：尚未运行真实 B 仓库查询，没有生产 MQTT 认证、C 入库、心跳、控制或 E1 证据。远端门禁以最新提交实际结果核对，不能沿用上一提交的全绿。

## 隔离 MQTT 接收包与注册适配验证

新增 `scripts/windows/check-mqtt-registry.ps1`，实际运行 broker 合法发布/订阅和权限拒绝检查，使用 `mosquitto_sub -N` 保留接收字节，在容器内 `cmp` 核对原文件的每个字节（含末尾换行），再导出真实接收包。此前 PowerShell 文本比较去掉末尾换行，只证明文本匹配；本轮已改为严格字节比较，重新实测通过。

接收包随后在一次性 PostgreSQL 中回放到注册适配器：创建明确测试设备，读取数据库内管理员，绑定固定测试仓库 ID，新 session 查询后校验设备/绑定/原文/服务器接收时间。真实 B 仓库查询仍是明确替身，未写 C 观测表。此过程是隔离的接收包验证，不是生产持续监听或完整 E1。

2026-10-12实跑通过；本机私有原包证据 `work/a-mqtt-receipt-5eb3992d4aa347959b5d3052bbaf231a/mqtt-receipt.json`。broker、临时 DB、网络清理后只保留原包文件。脚本拒绝在非 `test-db/freshlink_test/FreshLink-test` 环境写测试设备。

复现（从项目根目录、固定镜像已获取）：

```powershell
powershell -NoProfile -File .\scripts\windows\check-mqtt-registry.ps1
```

Docker 不在 PATH 时可传 `-DockerExecutable` 指向实际 docker.exe。使用已有测试 Compose，不改主运行 Compose 或生产配置。

## 下次接续

先核对 B/C 最新回复、PR #14 审查与当前 main。B 就绪后接真实仓库查询/绑定 HTTP 并补权限、故障和联调测试；C 就绪后接真实持久化。依赖未齐时继续 A 自己的仿真协议和控制失败测试，保持共享阻塞明确，不用替身冒充正式功能。
