# C 隔离 PostgreSQL 候选验证入口

2026-10-17，A 维护共享测试接线；不修改 C 服务/测试、不新建正式迁移。

C 的 `test_telemetry_storage_postgres_candidate.py` 在普通套件内默认 opt-in
跳过，不能把整仓绿灯称作真实 PG 通过。本入口保留其严格连接守卫，只连接
本次临时 `test-db/freshlink_test/FreshLink-test`；没有真实配置、主机端口、
持久卷或外部通知。唯一随机 schema 由 C 测试自行创建/非 CASCADE 清理。

## 本机 Windows

已有 Docker Desktop Linux 引擎和缓存 `freshlink-app:p0`（项目 Python3.14
依赖）时，在**含 C 候选源码**的正确工作区运行：

```powershell
powershell -NoProfile -File .\scripts\windows\check-c-candidate-pg.ps1
```

脚本不安装依赖、不触碰宿主 Python、不拉新镜像；缺镜像/源码/引擎即明确停止。
每次创建独占名称的内部网络、PG18 tmpfs 测试容器，运行当前工作区两项测试，
并另查 `c_test_%` schema 剩余数为0，finally 仅清本次专属项目。
当前分支有未提交改动时实际测试包含该改动；记录 HEAD、dirty 状态和输出，
不要声称只测了某个远端 SHA。数据库新连接检查不是数据库进程重启检查。

## GitHub

新增 `Test C Candidate PostgreSQL / c-candidate-postgres` 为独立可见 job：
标准 Ubuntu24.04 runner、Python3.14 job 容器、同 job 私有网络 PostgreSQL18
服务别名 `test-db`，仅 contents:read；不部署、不上传附件、不使用真实 secrets。
云端依赖仅在可丢弃 `/tmp/freshlink-c-ci-venv` 安装，锁文件保持 `--locked`；
**绝不照搬云端同步命令去宿主 D 盘 Python 环境运行**。

PR 涉及 backend/contracts/C文档/锁或本 workflow 时运行，也可手动触发。
如果当前提交没有 C 的 opt-in 测试文件，明确输出“源码不存在、未验收”，
不会把不存在的测试宣称通过。测试仍为候选范围；正式服务上线另走审查。
新 job 不代替已有全后端/覆盖率/安全/浏览器门禁，不扩大付费/部署权限。

验证内容：原文/重复/冲突/回滚/并发/新连接；实际 T1 未提交旧时间数据，
旧快照页/total/CSV 不变，新快照见到晚提交记录。不是正式 Alembic、
鉴权 HTTP 下载、B 接收或通知/E1。代码变化后以本次 HEAD 的 job 原文为准。
