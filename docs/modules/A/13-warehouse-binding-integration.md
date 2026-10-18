# A/B 接线候选：真实冷库、一次绑定与控制

2026-10-15，PR14 的共享候选，未合 main、未部署，不代表 S0/E1 完成。
正常合并 B `1e5e005970e5cdeca7fcf6a4210e0857787f85b1` 与 A05
`cd2b9bfdbd5054d616f86849a0e30fa526c837b5`，不带旧私有历史，不改 B/C 源码。

## 接口和权限

- 主应用只在 `app/main.py` 加一次 `/api/v1`；B 聚合器及 A 路由不再加该前缀。
- B `/warehouses/` 等路由真实挂根；`POST /candidates` 和 `/device-events`
  临时只给活跃管理员。服务专用认证仍待正式接线，普通用户只读，匿名不放行。
- 设备注册仍不接未经验证的仓库绑定。新增
  `POST /api/v1/devices/{device_id}/binding`，正文仅 `warehouse_id: UUID`，
  多余字段拒绝。管理员成功返回200及 DevicePublic；匿名401，普通用户403，
  非法UUID/正文422，设备或冷库不存在404，换绑另一冷库409。
- 同一设备/冷库重复提交安全；行锁沿用 `bind_registered_device`，不静默搬仓。
  绑定不标在线，不改历史 `held_unbound` 事件、不重建来源 ID、不改既有控制证据。
  未来换绑/仓库删除策略另行确认；当前 B 没有仓库删除 API，本轮不添加跨模块外键。
- 数据库读/提交/提交后刷新故障返回503及 `Retry-After: 30`；失败 Session 回滚。
  提交可能已经成功时，客户端仍重试同一绑定正文，不假称首次没有写入。
- `warehouse_lookup_for_session(Session)` 调实际 B `get_warehouse`；只把
  WarehouseNotFound 变 False，数据库故障传播。控制 HTTP 和 worker 使用同一
  工厂，不设置恒 True/default-fake，不自动创建幽灵冷库。

## 实际验证

缓存容器 Python3.14.8；独立 PostgreSQL/tmpfs，内部认证 Mosquitto，无主机端口。
原业务数据库、宿主 Python/环境变量、真实配置和外部收件人未改。

1. 完整后端 **584 passed / 95%**，包括 B 226 项既有回归和根接线/绑定18项。
   桥接、绑定路由在本轮语句覆盖均100%，不表示穷尽并发或安全场景。
   权限、UUID/额外字段、未知对象、重复、换绑冲突、历史不改、数据库失败和
   “提交已成但刷新失败后同正文重试”均有回归。绑定事务的正式 PG 并发专项未新增。
2. `check-control-mqtt.ps1 -WithWarehouses` 实际执行：B HTTP创建冷库 →
   A HTTP绑定两设备（未知冷库拒绝/同绑定重试/仍未知状态）→ 心跳在线 →
   管理员HTTP命令 → PG队列 → MQTT → 软件制冷执行 → ACK → PG/API。
   **HTTP没有lookup override，worker使用真实B工厂**。观察到实际后续MQTT温度下降，
   仅代表仿真行为，不是实物冷库、品质或节能效果。
3. 重放原包执行仍一次；controller伪造ACK发布被拒；停止/重建worker后原命令
   从持久队列恢复；超时后的真实expired ACK保留late证据，不能改为成功。
4. 本轮先遇到 SQLite 回读时间失去时区，故没有修改生产时间规则去迎合SQLite；
   实际控制创建/执行验证放在真实PG。类型检查缓存改用容器临时目录，未安装工具。
5. 最终绑定DTO的18项根路由复测通过；完整app mypy（57文件）、Ty与范围Ruff通过。
   从已通过后端导出的schema更新客户端，锁定Bun容器生成SDK、Biome81文件、
   TypeScript/Vite构建通过，产物保存在work。没有运行本轮浏览器联合交互。
   原A04无开关fixture模式亦完整复跑通过，没有把其结果混作真实B lookup。

证据：`work/a01-tests-20261015224716-12880/pytest.xml`；
`work/control-mqtt-20261015224728-47884/mqtt-control.json`，SHA256
`b4c70b44521e5a800645d36e8c557e8046c8fdbfb210ee1a3b4f80372d7ae53b`。
这些是本机私下证据路径，不上传配置、数据库或日志。后续同提交CI另行回读。

## 复测及未完成

在项目目录运行 `powershell -NoProfile -File scripts/windows/check-control-mqtt.ps1 -WithWarehouses`。
此开关只在确认 host=`test-db`、数据库=`freshlink_test`、项目=`FreshLink-test`
之后，**测试专用**创建一张真实B Warehouse表；不是B正式Alembic，不向应用库create_all。
默认无开关仍保留原A04显式fixture模式，两种证据的mode不可混用。
脚本只清理本次唯一临时项目，恢复本进程变量，失败保留work证据。

本轮未完成：B正式唯一迁移/告警处置/通知与页面；C真实PG/历史CSV/页面；
绑定管理页面、正式worker运行入口、生产TLS/ACL、跨机与14设备、两通知实收、E1。
控制的告警引用仍拒绝未经验证的event_id。不要以本候选绕过三方正式迁移/契约审查。

B同版复审：上轮两个故障修复和226项回归已核；F02临时读取故障误判永久无效
已独立复现交原作者；另复现有界后继过期后旧规则错误复活，须统一reader/writer
派生闭窗；severity三值收窄待B。契约恢复样例仍待B/C，不宣布冻结。

## 2026-10-18：同步最新 B 修复后的复核

本节优先于上文历史候选。A 自有集成分支正常合入准确 B
`7b1cf26d9ad1e7ec52b41654550ad2615bc097f9` 和已发布共同 main
`095c3a7b34ceacd4b9d52cbea180d77de596f2dc`，运行候选为
`52ed0ea9fe9e7534e8005ff0d2a8f9b392e725ee`；没有修改 B 原分支或强推。
这避免把旧规则闭窗、暂时读故障和 severity 缺陷随 A 接线重新带入。

准确候选在缓存 Python 3.14.8／PostgreSQL 18.6 的专属临时库实测：
完整后端 **603 passed／95%**（含根接线18项）；实际 B 冷库 HTTP 创建、
A 单次绑定、真实 B lookup、管理员控制到认证 MQTT／软件设备原 ACK／
PG 与 API 终态通过，包含重放、拒绝伪 ACK、超时与晚 ACK。
测试服务／内网已清理，原应用库及已恢复的宿主 Python 未操作。

以上仍是待审集成候选。B 表的 DDL 是**隔离测试专用**，不是正式 Alembic
迁移；发布链仍只有 `a03_control_storage`。未合入 main、未部署、未完成
C 正式接线／浏览器／真实两通知或 E1；各自按 #5／#6／#7／#12 继续验收。
