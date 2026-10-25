# A53：四个真实遥测进程和 TCP B 接口的受控组合

依赖 PR65 精确466868；不修改其头，也不碰 Claude 的两个派发文件。只是既有运行入口
的实际组合验证，不新建业务模型、迁移、队列或通知运行器，不代表完整 M02/E1。

在既有缓存 Python3.14.8 镜像、专属 PostgreSQL18、认证 Mosquitto 的 internal 网络中，
沿唯一候选 Alembic 链升到 C01 并 model check。拒绝任何预存业务行；账户仅为新建
合成管理员。绑定和批次/规则用实际 HTTP；设备固定 ID 为 Broker ACL 夹具，不能
据此签收设备注册页面。origin/epoch 通过原显式空库初始化器，不自动猜测或修补。

API 是实际 Uvicorn 子进程，仅容器内127.0.0.1:18080，不映射宿主端口。receive、
store、evaluate、deliver-high 各用原 telemetry_service CLI 启动独立 Python 进程，
不以对象方法调用替代。JWT 从真实登录取得，只传给内部高温投递进程环境，不写
JSON/命令行/日志；MQTT 密码只传接收进程。原始包重复使用同 message_id，以真实
去重保证一次观测/一次 B 告警；后续读取真实 JWT HTTP 历史/raw 和固定快照 CSV。

未初始化流的 store 进程应退出1并返回固定脱敏配置失败，且不产生流/观测；随后
显式初始化，四角色完整跑通。清理先逐个停所有本轮子进程，记录每个 exit code，
任何强杀都失败。Uvicorn 在完成 ASGI 关停后会重新抛出本人发出的 SIGTERM，API
的-15如实记录，不能冒称0；四业务进程必须0。数据库/网络由原隔离脚本最终清理。

运行方式（当前源码候选，不是安装或部署）：

```powershell
pwsh -NoProfile -File scripts/windows/check-gated-telemetry.ps1 -AppImage <已有缓存应用镜像> -RuntimeProcesses
```

此模式与 ControlEntry 互斥，禁止把两次独立结果拼为一次完整控制闭环。默认旧模式
不变。证据为私有 work/gated-*/runtime-composition.json、runtime-child-cleanup.json、
cleanup.json；其中source_revision/dirty、子进程退出、未测范围均保留。

初跑确实失败：B source_event_id 为字符串、C 为 UUID，验证器直接比较类型不同；
后改为双方 UUID 规范化比较同一身份，不放弃来源一致断言。另如实区分 Uvicorn
SIGTERM 正常关闭与业务进程退出0；未修改业务/接口/状态机。修正后实际串联通过。

仍未证明：安全恢复、任何真实通知/终端实收、该次控制、浏览器、14设备、第二台
电脑、正式发布或比赛投递。外发0、原数据库/宿主 Python 不动。最终精确提交的
实跑位置/hash在 PR/实链 #6 更新，不把首次 dirty 证据改写成 clean-head 运行。
