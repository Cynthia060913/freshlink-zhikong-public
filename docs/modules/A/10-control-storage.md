# A03 控制事务、回执与权限 API（本地候选）

2026-10-14，继承A心跳 `07aac839c24bd6acb41003aa28a65fb672a0bf65`，
独立 `feature/a-control-storage`。未合共同main、未升级应用数据库，
F05正式签收和共享迁移顺序仍需三方确认。以下是本轮实际状态，不宣布R03/E1完成。

## 保证与明确限制

- 管理员、有效绑定和90秒内可信在线心跳才允许新命令；发送前再次核对
  管理员权限、绑定和在线。不通过页面/设备自报仓库建立绑定。
- 客户端为一次意图提供稳定UUID，重试同ID/同内容返回原命令和原期限；
  同ID改参数/设备/用户/期限冲突409。issued_at/expires_at由服务器决定。
- action仅set_cooling，enabled必须真bool，生命周期1—300秒。告警event_id
  目前只允许null；未接B存在/同仓库校验前拒绝非空引用，不接受任意ID。
- Device锁串行命令，每设备最多一条queued/sent，数据库部分唯一索引兜底。
  旧投递不确定的failed命令在期限内还可能到达，因此不允许立即下相反新
  命令；明确执行失败ACK或旧期限已过才解除该不确定保护。
- 创建先保存原始受控命令包；发送前保存30秒租约和尝试ID，网络发送在
  事务外。重启租约恢复仍发原包/原ID，最多10次，1/2/4/8/16/30秒退避。
  存储故障回滚并上抛，不返回假queued/成功。
- Broker接受只记录transport_accepted_at/sent，不表示执行。真实ACK可早于
  PUBACK；后到的发送结果不能把acknowledged降回sent。
- 只有经可信Broker/ACL验证的设备身份可调用ACK服务；检查topic、设备、
  command_id、非retained、4096字节、唯一JSON成员、版本、时间和执行状态。
  成功执行状态需与命令一致；没有发送尝试不能凭空ACK。
- 超时固定在命令期限，晚到ACK保存late证据，不把timeout改成功。
  同ACK重发duplicate；不同回执保留conflict，不覆盖首条。每命令最多32
  个不同有效回执证据，保留原指纹和已有重复处理，不默默删去重记录。
- DeviceCommand、CommandAttempt、CommandReceipt在PostgreSQL持久保存。
  每设备10000条命令为行数工程上限，非字节/性能承诺；命令历史存在时
  设备删除受FK RESTRICT保护，账号删除也不级联抹掉控制记录。
- enabled/parameters是请求意图，历史ACK是当时执行证据，均不能当作
  仿真器重启后的当前物理开关状态。没有当前状态观测时不得显示“已开启”。
- 服务没有修改既有命令包的API；未安装数据库不可变触发器，不宣称抗篡改。

## HTTP 和B只读对接

`/api/v1`由外层app添加一次，本轮根路由已注册独立control router。

| 操作 | 路径/行为 |
|---|---|
| 创建 | POST /api/v1/devices/{device_id}/commands；201记录，非执行成功 |
| 查询 | GET 同路径；普通活跃登录用户可读，skip≥0、limit1—100 |
| 详情 | GET /api/v1/devices/{device_id}/commands/{command_id}；含回执追溯，跨设备ID/未知404 |
| B内部只读 | control_storage.get_command(session, command_id)，调用方先鉴权；None表示不存在，不自动写/超时 |

请求字段command_id、action、enabled、lifetime_seconds、event_id；返回包含
parameters、UTC时间、status、发送次数、Broker接受/ACK/结束时间和failure_reason。
命令还处于queued不代表制冷已改变。无人启动发送/超时处理worker时，读取不会
自行伪造状态转换；正式应用不能在缺worker时宣称控制服务正常。

创建依赖 `control_routes.control_warehouse_lookup`，目前明确503
warehouse_service_not_integrated + Retry-After30。集成方需提供使用实际B
仓库服务、实际request Session的依赖，验证有效UUID。独立HTTP正例测试
使用标记清楚的假lookup；不代表B已接线。生产严禁用恒True或直接写数据库
绑定来绕过此依赖。保持当前全部活跃用户共享只读、管理员写政策，未实现逐仓ACL。

未登录401、普通用户创建403；参数422；冲突/未绑定/不在线409；创建过程
存储故障503；到命令数量上限429。GET仍沿框架处理存储异常，不返回假空列表。

## 实际证据

- 完整后端296 passed/95%，新增控制41项，控制服务行覆盖96%。
- 实际PG并发同ID创建/发送租约、权限撤销、重复冲突、期限、晚回执、
  Broker与执行乱序、重试预算、32条证据上限、事务失败回滚、API权限测试。
- 实际软件设备执行保存的命令并生成ACK，后续模拟温度下降、功率变化；
  重放返回原ACK。此用例没有Broker，不能写成网络完整联调。
- 临时PG先A01合成记录后升级A02→A03，alembic check无新增操作；真实
  数据库容器重启＋新runner后恢复原命令/租约/设备原ACK，执行次数仍1。
  另命令迟到ACK保留timeout。B仓库是显式测试替代，没有通知外发。
- SDK/Biome/TypeScript/Vite构建通过并保存产物；没有新增控制UI/浏览器验收。
- 最初Field严格校验声明不受SQLModel支持，改为StrictInt；同刻首采样被设备
  合理拒绝，修正测试先后时间；未跳过测试或降低90%门禁。

详细证据：`work/a01-tests-20261014051512-41100`、
`work/hb-storage-20261014051100-16796/control-restart.json`、
`work/a01-frontend-20261014-051117-35500.json`。
测试记录是合成受控场景，不是实物温度或真实品质/节能率。

复测 `scripts/windows/check-a01-acceptance.ps1` 和
`scripts/windows/check-heartbeat-storage.ps1 -ControlCheckpoint`。使用缓存
Docker和唯一临时PG；清理仅该合成测试卷，原应用数据及系统Python未动。

## 接续顺序

1. 服务端可信MQTT控制发送/ACK监听和独立超时扫描，实际主程序往返。
2. 接实际B仓库依赖/绑定及告警引用；故障事件投B，三方确认迁移链。
3. 设备控制页面、错误/重试/超时/历史展示和浏览器实测。
4. 一库真实E1→扩十四仿真设备失败场景→第二机与同版提交材料。

本地旧仓库提交含继承历史；公开迁移要使用脱敏快照或审查后的安全补丁，
不能直接push此分支或私有bundle到新的干净公开仓库。
