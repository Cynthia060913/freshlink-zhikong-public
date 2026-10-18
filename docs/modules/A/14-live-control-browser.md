# A：真实浏览器控制专项（候选验收入口）

2026-10-19，基于 A/B 接线候选 PR14 / 29589bb；不是发布部署入口。
本入口将原来分开的控制 UI Mock 测试和 MQTT/API 测试连成真实浏览器专项。
保留原测试，不把专项通过解释为完整 E1。

## 边界和防误操作

- 必须已有缓存应用/PG/Broker 镜像、Node、Playwright 和 Chromium；脚本不安装软件，
  不运行宿主 Python/uv/pip，不修改用户/系统环境变量。
- 新建唯一 `control-browser-时间-PID` 测试项目，PG 使用 tmpfs；数据库和 Broker
  只接内部网络，不映射数据库/MQTT端口。应用另接浏览器入口 bridge，HTTP仅随机
  `127.0.0.1` 端口；这不是整个应用容器的网络出站防火墙。
- 迁移前检查 Docker、精确测试库/账号/项目、development、SMTP/Sentry禁用、完整源码SHA；
  不通过则不迁移。源码只读，容器临时副本运行，真实配置和原应用库从不挂载。
- 仅测试库创建 B `alerts_warehouse` 一张表，再通过真实 B HTTP 创建仓库、A HTTP绑定，
  控制使用同 Session 的实际 B lookup，**没有恒True/依赖覆盖**。
  这不是正式 B/C Alembic，不能复制成应用启动方式。
- 浏览器只允许同源网络请求，不伪造 API 响应；普通账号、邮箱和口令均为合成 fixture。
  无真实通知，无自动上传截图/报告，无部署。
- 结束只清理该唯一项目；原报告使用 CreateNew，宿主另存清理 sidecar，不覆盖容器原证据。

## 前端包的必要条件

旧 `check-a01-frontend.ps1` 只验证编译，曾让 frontend/.env 的开发地址
`localhost:8000` 进入保存的 bundle；这种包不能直接服务于任意测试端口。
当前脚本在一次性 Bun 容器中显式设置 `VITE_API_URL=`，生成同源包，
并在结果中记录 `api_origin`。不修改开发 .env，不手工替换压缩 JS，
不把测试包登录失败误报为服务端认证错误。正式 Dockerfile 已有独立的 VITE_API_URL 参数。

构建仍需指定与当前后端同源且实际通过的隔离 OpenAPI 目录，不能自动选到另一候选的 schema。
浏览器入口要求 `BundleSource:frontend` 与当前 HEAD 的 frontend tree 相同；
编译输入/依赖和构建环境仍需连同构建报告核对，树相等不是完整可复现构建证明。

## 运行方式

在项目目录先使用 `scripts/windows/check-a01-frontend.ps1 -EvidenceDirectory <同源通过的证据目录>`
生成保存在 work 下的同源包，再运行：

```powershell
powershell -NoProfile -File .\scripts\windows\check-live-control-browser.ps1 `
  -BundleDirectory "<本仓库work下的同源前端产物目录>" `
  -BundleSource "<该前端源码的完整提交SHA>" `
  -NodeExecutable "<已存在的node.exe>" `
  -PlaywrightModules "<已存在的含playwright的node_modules目录>" `
  -ChromiumExecutable "<已存在的Chromium可执行文件>"
```

不具备缓存依赖的机器先报告缺口，不为了本专项修改宿主环境；该工具不是新增 S0 前提。
守卫回归 `backend/tests/api/routes/test_live_control_browser_guard.py` 已放入现有后端测试收集路径，
本机仍只在容器中运行，不另要求队友运行宿主 Python。

## 通过判据与证据

1. 浏览器真实登录、设备页/详情加载；准备和取消不发控制 POST。
2. 点击确认产生一条实际 HTTP 命令，PG队列→认证MQTT→仿真执行→真实ACK保存→页面回读。
3. 原编号同文 HTTP 重放、刷新后读取同一条回执；命令行数/设备执行次数仍为1。
4. 普通账号真实登录能读历史，页面不提供新控制，实际POST返回403且不新增命令。
5. 实际 MQTT 温度采样在制冷后下降；这是仿真变化，不是实体冷库/品质/节能效果。
6. 浏览器、服务端和清理报告分别保留；失败历史不删除。报告中的 source 必须对应准确提交。

实际运行结果/精确SHA在 PR 和 #6/#7 发布，未运行不得凭本文判据宣称通过。
仍须另验：正式 B/C 迁移、C 实时历史CSV/页面、异常恢复、两种独立真实通知实收、
同一发布版 E1、14设备、第二机和完整参赛材料。截止仍为10月30日及之前实际投递。
