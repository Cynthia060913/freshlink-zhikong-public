# 第二机验收准备（可执行步骤 + 待填写记录）

依据固定提交 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`。**本页是 A 准备的方案和空白记录，
不能代替 C 在第二台电脑实际运行，也不能勾选“通过”。** 所有“通过”只能由 C 在另一台电脑
按同版交付物实测后填写。命令与页面依据见 `A-SYSTEM-DESIGN.md` 与对应 `check-*.ps1`。

## 1. 另一台电脑准备清单（安装/改系统需本人同意）

- [ ] Windows，Docker Desktop 用 **Linux 容器（WSL2）**；Docker CLI + Compose 可用。
- [ ] PowerShell（源码导出回归在 PS 7.6.5 实际通过；PS 5.1 兼容未验证）。
- [ ] git（核对版本用）。以上用 `scripts/windows/check-environment.ps1` 一次查齐。
- [ ] 安装 WSL/重启/改防火墙均需本人同意；不关闭证书校验、不装来源不明的修复包。

## 2. 获取同版交付物并核对版本/哈希

- [ ] 与 A 确认同一冻结版本完整 40 位 SHA，**不以旧 main 或“最新”代替**。
- [ ] 从该版本导出源码候选：`.\scripts\windows\export-source-candidate.ps1 -Revision <SHA>`
      （输出到 `work/source-candidates/`，含源码 ZIP、`manifest.json`、逐字节核对）。
- [ ] 记录源码 ZIP 的 SHA256 与 `manifest.json`，与 A 给的核对一致。
- [ ] 离线 fleet 包（若使用）在另一分支 `claude/package-safety-fix`，用其
      `run-offline-fleet.ps1 -ExpectedManifestSha256 <哈希>` 先做只读传输完整性核对；
      其状态仅是 `transport_candidate_...`，**不是 E1 或第二机验收**。

## 3. 干净隔离启动（不能沿用 A 的私有库/配置）

- [ ] 在第二台电脑新生成自己的 `.env.freshlink`（`start-local.ps1` 首次自动生成），
      **不复制 A 的数据库卷、`.env.freshlink`、`work/` 或缓存**。
- [ ] 启动：`powershell -File scripts/windows/start-local.ps1`，再 `smoke-test.ps1` 验证登录。
- [ ] 记录这台电脑的容器/镜像 ID、启动日志路径与时间。

## 4. 最低验收步骤（按业务链，不是只“网页能开”）

| 步骤 | 操作与页面/接口 | 应看到 | 依据 |
|---|---|---|---|
| 登录 | 打开 `http://localhost:18000`，用本机 `.env.freshlink` 账号登录 | 进入主页，非匿名拒绝 | `smoke-test.ps1`、`/api/v1/login` |
| 设备 | `/devices` 注册并绑定设备，看在线/离线 | 设备列表与在线状态 | `modules/iot`，`/api/v1/devices` |
| 历史/CSV | `/telemetry` 查看原始/清洗数据，按同一筛选导出 CSV | 历史与 CSV 同快照一致 | `/api/v1/telemetry/observations` |
| 控制与 ACK | 下发制冷命令 → 设备执行 → 原 ACK → 服务端回执 | 命令状态变化，后续温度反映制冷 | `/api/v1/devices` 控制路由 |
| 异常追溯 | `/alerts` 触发异常、处置并回看时间线 | 异常与处置可追溯，历史不被清 | `modules/alerts` |

> 这些是**待执行步骤**：完整业务链目前尚未全部接线，能否逐条通过要以冻结版实测为准，
> 不把源码存在或 CI 当真实效果。缺接点如实记到“失败摘要”，不勾选通过。

## 5. 双通知验证的授权前提与实收记录位置

- [ ] SMTP 与飞书两个独立渠道，发送账号由**持有人本机**配置并授权，收件由接收人本人确认；
      授权就绪不等于已授权 Agent 发送，也不等于终端实收。
- [ ] 实收证据（脱敏：隐藏邮箱前缀、手机号、授权码、机器人地址、二维码）记录相对证据位置，
      不外发原件。

## 6. 结果填写（每项都记，缺一不可）

| 字段 | 示例/要求 |
|---|---|
| 时间 | UTC 与本地时间都记 |
| 电脑 | 系统版本、Docker/镜像 ID、是否第二台 |
| 版本 | 完整 40 位 SHA + 源码 ZIP SHA256 |
| 步骤结果 | 登录/设备/历史 CSV/控制 ACK/异常追溯 各自实际结果 |
| 证据 | 相对路径（截图/日志/导出文件），不复制秘密内容 |
| 失败摘要 | 哪步失败、日志位置、是环境还是业务缺陷、未修前不标通过 |

**结论只写“第二机已完成哪些、未完成哪些”，不写“整个项目已通过”。**
