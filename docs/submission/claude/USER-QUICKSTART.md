# 鲜链智控 · 最短运行说明（给不熟悉工程的同学）

依据固定提交 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`（A 待审组合，**不是 main /
最终发布版**）。本页只讲“需要什么、怎样启动、看到什么算成功、失败后怎么办”，命令均来自
仓库现有脚本，未验证项单独标注，不把分段验证写成完整业务系统。

## 1. 先分清四件事（很容易混）

| 层次 | 实际入口 | 证明什么 | 不是 |
|---|---|---|---|
| 网页应用启动（P0 模板） | `scripts/windows/start-local.ps1` + `compose.freshlink.yml` | 容器、登录、健康检查能跑 | 冷库业务没验收 |
| 一段隔离验证脚本 | `scripts/windows/check-*.ps1`（设备/遥测/MQTT/告警等） | 该脚本限定的合成场景 | 不等于完整实链 |
| 离线 fleet 验证包 | `claude/package-safety-fix` 分支的 `export/run-offline-fleet.ps1`（**不在本提交**） | 内容寻址包的传输/一次性隔离证明 | 不等于 E1 或第二机 |
| 完整业务系统 | A/B/C 各候选，尚未全部接线 | 两库三批十四设备的端到端 | 当前未签收 |

## 2. 必需环境

| 项 | 版本依据 | 说明 |
|---|---|---|
| Docker CLI + Compose | `check-environment.ps1` 检查 | 需要 **Linux 容器**（WSL2）；仅装 CLI 不够 |
| Docker 引擎 | 同上，要求 `OSType=linux` | 引擎未跑或不是 linux 会直接退出 |
| PowerShell | 脚本用兼容语法；源码导出回归在 PS 7.6.5 实际通过 | PS 5.1 被本机执行策略阻止过，未宣称兼容 |
| git | `check-environment.ps1` 检查 | 团队开发需要 |
| 宿主 Python | 容器模式**不需要**宿主 Python | Python 3.14 / Bun / PostgreSQL 18 都在容器内 |

未知/未验证：B、C 两台电脑的复现结果；PowerShell 5.1 兼容性。

## 3. 目录、候选版本与私有配置

- 项目根目录 = 本仓库检出目录；候选版本用完整 40 位 Git SHA 唯一标识。
- `compose.freshlink.yml`：本机 P0 开发入口（`db`=postgres:18，`app`=从 `backend/Dockerfile`
  构建的 `freshlink-app:p0`），只绑定 `127.0.0.1:18000`。
- `.env.freshlink`：**私有配置**，由 `start-local.ps1` 首次生成（`POSTGRES_PASSWORD`、
  `SECRET_KEY`、`FIRST_SUPERUSER_PASSWORD`），不进 Git、不发给队友。
- `work/`：运行日志与验证证据目录，私有，不进源码包。

## 4. 最短真实路径（按顺序）

```powershell
powershell -File scripts/windows/check-environment.ps1   # 只读检查环境
powershell -File scripts/windows/start-local.ps1          # 构建并启动（首次联网下载）
powershell -File scripts/windows/smoke-test.ps1           # 校验 API/登录/当前用户
```

或一次组合（自动留日志与结果）：

```powershell
powershell -File scripts/windows/start-and-verify.ps1     # 结果写到 work/p0-start-*.json/.log
```

停止保留数据：`powershell -File scripts/windows/stop-local.ps1`；**没有“一键删库”操作**。

## 5. 启动后看什么算成功

打开 `http://localhost:18000`，账号 `admin@example.com`，密码从本机 `.env.freshlink`
的 `FIRST_SUPERUSER_PASSWORD` 读。`smoke-test.ps1` 通过三项即 P0 成功：

1. `GET /api/v1/utils/health-check/` 返回 `true`；
2. `/` 返回 200 且 HTML 含 `id="root"`；
3. 用该账号登录并取到 `admin@example.com` 的超级用户身份。

注意：这证明**模板网页与登录可用**，不证明任何冷库业务、MQTT、告警或第二机。

## 6. 常见问题排查

- **Docker 未运行 / 非 Linux 容器**：`check-environment.ps1` 退出码 2 并打印缺项；先启动
  Docker Desktop 并切到 Linux containers（WSL2），不要关证书校验或防火墙解决下载。
- **端口占用**：`compose.freshlink.yml` 用 `127.0.0.1:18000:8000`；18000 被占时 `up` 失败。
- **认证失败**：确认读的是 `.env.freshlink` 里的 `FIRST_SUPERUSER_PASSWORD`，不是凭记忆。
- **版本不一致**：启动、验收、材料必须对齐**同一个**完整 SHA；别把旧 main 当最新。
- **缺私有配置**：`start-local.ps1` 首次会自动生成 `.env.freshlink`，不会打印密码；不要手工填真实密钥。

失败保留日志交给 Agent 分析；**不要重置数据库重试**。

## 7. 哪些要本人批准、怎样留证据

- 需要本人批准：安装/升级 Docker 或 WSL（可能需管理员与重启）、对外发布、发送邮件/飞书、
  部署到服务器、真实参赛投递。本机启动不等于获准发布。
- 保留证据：`start-and-verify.ps1` 自动写 `work/p0-start-<时间>-<PID>.log` 与同 `.json`；
  各 `check-*.ps1` 把证据留在私有 `work/`，失败也保留来源与错误，不清理。

## 8. 还没接好的接点（不瞎补命令）

- 完整业务链（设备→A 接纳→C 存储→B 告警→两通知实收）尚未全部接线，责任在 A/B/C 各自主任务。
- 离线 fleet 验证包在另一分支 `claude/package-safety-fix`（`79de20b` 起），本提交不含，
  引用前需单独核对该分支说明 `docs/submission/OFFLINE-FLEET-KIT.md`。
- 第二机从交付包复现、两通知终端实收、PPT/视频与最终投递：均未签收，见 `docs/submission/README.md`。
