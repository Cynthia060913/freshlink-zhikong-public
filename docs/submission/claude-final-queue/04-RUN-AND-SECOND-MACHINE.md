# 04 运行与第二机说明（开发方/接收方，复用旧运行说明）

软件来源 `bfb2c927f2a1a083ac21996844d38ac4eb4abbef`（候选，非已发布 main）。命令均来自
`scripts/windows/` 现有脚本，不新增/修改脚本、不执行部署。分层：P0 网页启动 ≠ 隔离验证 ≠
完整业务链，三者分开表述。

## 1. 必需环境（只读检查）

`powershell -File scripts/windows/check-environment.ps1` 一次查齐：Docker CLI + Compose、
Linux 容器引擎（`OSType=linux`）、git、PowerShell。容器模式不需要宿主 Python（Python 3.14 /
Bun / PostgreSQL 18 都在容器内）。安装/升级 Docker、WSL 或改防火墙由机器持有人决定，Agent 不执行。

## 2. 开发方（有完整 Git 检出）

按顺序在项目根目录执行：

```powershell
powershell -File scripts/windows/check-environment.ps1   # 只读检查
powershell -File scripts/windows/start-local.ps1          # 构建并启动（首次联网下载）
powershell -File scripts/windows/smoke-test.ps1           # 校验 API/登录/当前用户
powershell -File scripts/windows/stop-local.ps1           # 停止并保留数据库卷与配置
```

- `start-local.ps1` 固定 Compose 项目名 `freshlink`、`compose.freshlink.yml`，只绑
  `127.0.0.1:18000`；本机已有该实例时触及**同一个实例**，先只读核对，不删除/覆盖原数据，
  也不是随机隔离环境。
- 首次启动自动生成 `.env.freshlink`（`POSTGRES_PASSWORD`/`SECRET_KEY`/`FIRST_SUPERUSER_PASSWORD`），
  不进 Git、不发队友。账号 `admin@example.com`，密码读该文件。
- `smoke-test.ps1` 通过三项（health、首页 HTML、登录取到管理员）只证明模板网页与登录可用，
  不证明冷库业务/MQTT/告警/第二机。

导出与构建（对齐同一冻结版 40 位 SHA）：

```powershell
powershell -File scripts/windows/export-source-candidate.ps1 -Revision <40位SHA>
powershell -File scripts/windows/build-application-candidate.ps1 -Revision <40位SHA>
powershell -File scripts/windows/check-application-candidate.ps1 -BuildReport <application-build.json>
```

- 导出生成源码 ZIP + `manifest.json`（`work/source-candidates/<时间>-<随机>/`）；逐字节核对 Git 树。
- 构建生成唯一镜像 `freshlink-application-candidate:<SHA前12>-<随机>`；镜像 ID 与构建报告是
  来源声明，**不是**可运行交付物。
- `check-application-candidate.ps1` 新建随机内部网络 + tmpfs PG、无源码挂载、按默认 CMD 起应用，
  探针检查健康/登录/匿名拒绝/注册/重复拒绝/详情；只清理本轮资源。

## 3. 接收方（拿到包，不一定有 Git）

- 核对现成包的 `manifest.json` 与 SHA256 与 A 给的一致（不能重新导出时只能核对，不能复现）。
- **镜像导入目前没有现成脚本**：构建镜像只在本机构建机本地，交付需要镜像归档
  （如 `docker save`）或获准获取途径 + 启动配置与依赖说明，这一环仍是 M02 缺口，待补，不自行写导入脚本。
- 有 Docker 的接收机也可在本机重跑 `build-application-candidate.ps1`（同样需联网解析 build-system）。

## 4. 第二机验收记录（由真正另一台电脑的执行者本人填写）

同机新容器/新 Engine **不能**冒充第二机；本表是空白记录，不代填、不勾选"通过"。

| 字段 | 记录要求 |
|---|---|
| 电脑 | 系统版本、Docker/镜像 ID、确认是第二台 |
| 版本 | 完整 40 位 SHA + 源码 ZIP SHA256（与 A 核对一致） |
| 准备 | 自生成 `.env.freshlink`，不复制 A 的数据库卷/配置/`work/` |
| 步骤结果 | 登录 / 设备注册与在线 / 历史 CSV / 控制 ACK / 异常追溯 各自实际结果 |
| 证据 | 相对路径（截图/日志），不复制秘密内容 |
| 失败摘要 | 哪步失败、日志位置、环境还是业务缺陷；未修前不标通过 |

结论只写"第二机已完成哪些、未完成哪些"，不写"整个项目已通过"。

## 5. 需要本人批准与留证

- 需本人批准：Docker/WSL 安装升级、对外发布、发邮件/飞书、部署、实际参赛投递。
- 留证：`start-and-verify.ps1` 写 `work/p0-start-*.json/.log`；各 `check-*.ps1` 证据留私有 `work/`，
  失败也保留来源与错误。
