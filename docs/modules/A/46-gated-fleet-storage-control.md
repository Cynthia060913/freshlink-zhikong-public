# A46：两库三批十四设备的存储、控制与重启实测

2026-10-24 北京时间。依赖 PR58 的精确 `a3aae920403e102600fcd02e4b943150785e68e2`；
这是后续验证候选，不是共同 main 发布、完整 E1 或第二机签收。未改 B/C 业务、活动迁移、
宿主 Python、原数据库或真实配置，真实通知为零。

## 输入 → 实际处理 → 可复核输出

实际空库 Alembic A04→A05→C01 / model check，实际 JWT 登录和 B HTTP 创建两冷库、
三个苹果批次及历史规则。十四个注册设备 ID 是公开 Broker ACL 配套的显式测试夹具，
通过模型持久化后用实际 A/B HTTP 绑定，**不冒称本证明调用了设备注册 API**。
七组软件仿真温度传感器/制冷执行器通过认证 MQTT，七个显式 fresh-origin epoch，
A 持久接纳与同 Connection C 门禁存储、服务端在线状态、七次实际设备执行 ACK，
各执行器 ACK 后对应传感器的新样本降温。历史及固定 snapshot CSV 按冷库筛选，
比较完整 observation ID 集合，不只比较行数。

WH1 有两个活动批次，现有全局 observation job 的多批次语义未实现，因此四路评价
必须 held_unknown / one_active_batch_required；WH2 单批次三路正常评价 within_rule。
不循环单批接口、不随意取第一批、不把安全 hold 算作多批告警完成。

## 真实结果与失败保留

- 第一轮 `cf9af37140d8bd1bfd5a3d357ac0585efa44b070` 证明器传 poll_seconds=0.02，
  低于现有 worker 的 0.1 秒下限，启动被正确拒绝；没有修改 worker 限制。
  `work/gated-fleet-88a0c66e483c433ebcf44fec1fa62e17/runner.log` 保留，退出1、清理true。
- 参数修正后，干净 `18c1068359a62ff70c5562e4465adb252bc9cf18` 第一轮通过：
  14在线、7采集流、7实际执行ACK及后续降温；两库CSV分别20/15行且精确ID一致。
  `work/gated-fleet-2912b03666ef47b295c2cca593c3d231/gated-fleet.json`，SHA256
  `6f783fec21dcf718e9336192e8cf3ad6518e97310e0f8504a740f528dded3631`。
- 干净 `62bb2ef16088b6c02d2ee5d6982f3603f849684d` 增加失败/重启后通过：
  十四设备真实停发→服务端离线→同 SQLite 状态启动新 runtime→全部在线；每个设备
  恰好一条 offline 和一条 recovered，后者关联原 source_event_id；七路继续新采样，
  原 C 历史不丢，持久 execution_count 仍为1，未重复执行命令。两库CSV52/39行，
  完整ID与同冷库持久行集合一致。测试离线阈值3秒，生产默认90秒未改；不外推24小时稳定性。
  `work/gated-fleet-06ddc033cebc47149c7f46f08ee5aefc/gated-fleet.json`，SHA256
  `9549770f31745a6793c71f18a3123dca3a6e54420c0eb61cfdba5569914a6b85`。

每轮 test_exit=0 / owned_cleanup_ok=true；本轮独占网络、tmpfs PG 和容器已精确清理。
只使用缓存 Python3.14.8 应用镜像、PG18 和固定摘要 Mosquitto；当前源码只读挂载，
不称缓存镜像已含本轮源码。三个迁移前守卫测试通过，错误目标/宿主执行/真实 SMTP 等
在业务导入和迁移前拒绝。Ruff/format通过；本轮未重复跑无变化的1216项业务全套，
该既有回归的精确来源与范围见 A45。

## 复现与剩余出口

在本候选的干净完整提交上，确认已有所需缓存镜像后：

```powershell
pwsh -NoProfile -File scripts/windows/check-gated-fleet.ps1 -AppImage freshlink-application-candidate:274aecab58e5-9038157d1312
```

脚本拒绝脏源码、既存结果目录、宿主端口、持久卷及非内部网络，创建随机专属项目，
失败也清理并保存证据；不会自动 pull、安装依赖或启动真实外发。

尚缺：多批次原子业务评价、C高温异常恢复全写/发前复核、两渠道授权终端实收、
完整E1、第二机、同版最终材料。十四设备连通/控制/历史/重启通过不等于这些出口通过。
原20日20:00完整E1延期保留；24日距27日功能/实链/第二机冻结约三天。

另 A45 新增第二次完整浏览器成功（精确 a3aae920），server报告SHA256
`155de473900e3c5838a4460d4fb8e7f6bd2b73d669a6b7ecda53fbfe35cf3bc8`、browser报告
`9fd31bdf9d90d462e66ae5ba2b10e0627d21530bc56cd59bac96deb0856e63fb`，目录
`work/control-browser-20261024002315-10548`。此前首轮后台失败仍保留、根因未定，
两次后续成功不能称根治；浏览器合成B恢复也不是C自动恢复或真实通知。
