# B：本机与共同仓库就绪报告

> 落地位置：`docs/modules/B/00-readiness.md`（共同仓库 `feature/b-notification-bootstrap` 分支）。
> 角色：B（GitHub `xixidamei`）。
> 日期：2026-10-10。

## 0. 元信息

- 共同仓库：`https://github.com/Cynthia060913/freshlink-zhikong.git`
- origin 指向：Cynthia060913/freshlink-zhikong ✅（不指向 FastAPI 上游）
- 任务分支：`feature/b-notification-bootstrap`（基于 `origin/feature/a-device-minimal@a1f49ad`）
- A01+v4 实际基线：`a1f49ad6be8ad3c2213ffe9209bd4d7296324970`（远端 `feature/a-device-minimal` HEAD）
- main HEAD：`9db01387b1c4db2d0ff3f072a199134d7a64643f`（P0 模板，未含 A01 — **不要从 main 开新分支**）
- 公共契约版本：`contracts/INTERFACES.md` v0.1 草案（未冻结）
- git 身份：`user.name=xixidamei`、`user.email=257309821+xixidamei@users.noreply.github.com`（GitHub noreply 格式，**仅本仓库 local，未设 global**；旧用户名已由本人在 GitHub 改名，历史提交署名经重定向仍可关联）
- 接收对象（B 用户告知）：SMTP 收件地址为 B 本人真实邮箱，**为保护隐私不入库**，仅运行时经 `.env.freshlink` 注入；APP 推送凭据"后续补充"

## 1. 已完成（本轮）

| 动作 | 输入 | 实际 | 证据 |
|---|---|---|---|
| 只读检视公共资料 | AGENTS / START_HERE / docs/team/v4 / INTERFACES | 已读 | 见 `01-contract-proposal.md` 与本文件 |
| GitHub 私有仓库身份 | `git ls-remote --heads ...` | exit 0；5 个 refs 可见 | shell 输出（已记录） |
| 浅克隆 A 基线 | `git clone --depth 1 --branch feature/a-device-minimal ...` | 成功；HEAD=a1f49ad | `git log -1 --oneline` |
| 环境只读检查 | `pwsh -File scripts/windows/check-environment.ps1` | Docker 缺失；其余 OK | 见原文输出 |
| 开 B 分支 | `git switch -c feature/b-notification-bootstrap origin/feature/a-device-minimal` | 成功；分支指向 a1f49ad | `git status --short --branch` |
| 写本机就绪报告 | 本文件 | 已 commit working dir 草稿；现落到共同仓库 docs/modules/B/ | — |
| 写业务接口提案 | `docs/modules/B/01-contract-proposal.md` | 已写 | — |
| 写接入说明 | `docs/modules/B/integration-notes.md` | 已写 | — |
| 写通知适配器骨架 | `backend/app/modules/alerts/notifications/{transport,smtp,app_push}.py` | 已写 | — |
| 写单元测试 | `backend/tests/modules/alerts/test_{transport,smtp,app_push}.py` | 已写；通过 `python -m unittest` 验证 | 见测试报告 |

## 2. 已通过测试

- **SMTP 4 类**（`test_smtp.py`）：
  - 成功 → `accepted_by_service` / delivered=True / server_response="250"
  - 连接拒绝 → `transient_failure` / `"connection error"`
  - 超时 → `timeout` / `retry_after_s` 非空
  - 4xx SMTP 拒绝 → `permanent_failure`
  - 配置空 host 构造 → `ValueError`
  - 空收件人 → `permanent_failure`
  - envelope.kind ≠ SMTP → `permanent_failure`
- **APP 推送**（`test_app_push.py`）：
  - Disabled 默认 → `permanent_failure` / `"not configured"`
  - envelope.kind ≠ APP_PUSH → `permanent_failure`
  - 子类抛 TransientPushError → `transient_failure` / `retry_after_s=30`
  - 子类抛 PermanentPushError → `permanent_failure`
  - 子类抛 NotConfigured → `permanent_failure`
  - 子类成功 → `accepted_by_service` / delivered=True
- **契约**（`test_transport.py`）：
  - `NotificationEnvelope.source_event_id` 透传
  - `NotificationResult` 字段冻结

测试命令：

```bash
cd backend
python -m unittest discover -t . -s tests/modules/alerts -p 'test_*.py' -v
```

详见 B 用户授权后写入 work/ 的测试日志。

## 3. 阻塞 / 未完成项

### 仍然阻塞

| 阻塞 | 影响 | 需要做什么 |
|---|---|---|
| Docker 未在本机安装 | P0 起服 / S0 代码起点 / E1 联调都跑不了；B 第一轮纯逻辑测试可独立继续，E1 联调须在 A 那台执行 | 不变：等装 Docker 或在 A 那台 E1 |
| INTERFACES v0.1 未冻结 | B 第一轮已通过本地提案；正式 INTERFACES 版本由 A 本对话 Agent 汇总 | 等 A 公布 |
| GitHub Issue 创建 | 远程 B 主 Issue 等用户在 GitHub 网页创建并贴 `#num` | 用户在网页操作 |
| APP 推送凭据 | APP 推送适配器只能停留在 "DisabledAppPushTransport" 状态 | B 用户后续补充 token / 凭据 |
| 真实 SMTP 授权码 | 实发邮件不可执行（单元测试已 mock） | B 用户在 `.env.freshlink` 设置 SMTP 密码 |
| git 推送授权 | commit 之后才能 push | B 用户确认后授权我执行 |
| 公共根路由 | B 写了 `routes.py`，但 `backend/app/api/main.py` 不动；A 接力回合加 `include_router` | 已写 `integration-notes.md` |

### 已不再阻塞

- ~~等 A01+v4 推送 SHA~~ → 已抵达 `feature/a-device-minimal@a1f49ad`
- ~~B 第一轮分支入口~~ → 已开出 `feature/b-notification-bootstrap` 指向 a1f49ad
- ~~git 身份~~ → 已本仓库 local 设好

## 4. B 第一轮不会做（确认无歧义）

- 不重写登录、不动 iot、不动 telemetry、不动 analytics
- 不改主 Compose / 锁文件 / 公共导航 / 根路由 / 现有迁移
- 不持久化正式 B 模型（迁移轮到 B 时再写）
- 不写 `backend/app/modules/alerts/routes.py`（HTTP 路由由 A 接线；B 写 `integration-notes.md`）
- 不发任何外部通知（无授权）
- 不 commit / push（待用户授权）
- 不 merge main

## 5. 下一小任务

A 接力用户授权后，按以下顺序执行（B 不会自动越权）：

1. B 用户授权：`git add docs/modules/B backend/app/modules/alerts` → `git commit` → `git push -u origin feature/b-notification-bootstrap`
2. B 用户在 GitHub 创建 PR（base=main, compare=feature/b-notification-bootstrap）；Refs 远程 B 主 Issue
3. A 合并策略到位后 A 整合；B 不自行 merge main
4. B 阶段二（B1：仓储与规则 / B2：告警持久化去重 / B3：实收材料）按 `docs/team/v4/02-B-Agent指令.md` 后续小任务

## 6. 私有配置保护

- `.env.freshlink` 当前不存在（B 这台未生成）
- 已设的 git identity 是 GitHub noreply 邮箱（非真实邮箱）
- B 第一轮不从 `.env.freshlink` 读任何值（Settings 由 FastAPI 模板通过 `app.core.config.Settings` 注入）
- 真实 163 邮箱授权码（如果后续实发）必须仅在 `.env.freshlink` 设置，**永不**入 Git
- 已写代码中没有任何 SMTP 凭据 / APP 推送 token 硬编码

## 7. 环境更新（2026-10-13）

按 A 的要求（#5 6139486937）记录当前环境状态；不含任何敏感历史内容或凭据值。

### 版本与基线

- 任务分支 `feature/b-notification-bootstrap` HEAD：`bf2a652`
- 共同 main：`0bf33f8`（A 已按序合并 PR #14→#15→#16→#17，Actions 账单已恢复、main CI 转绿）
- B 分支演进：`1b9d94f`（CI 双根因修复）→ `b5aefce`（B3-1）→ `c2c8995`（B3-2）→ `423db0c`（凭据字面量清除）→ `bf2a652`（GCM 发帖助手）

### 本机环境与门禁（当前实测）

- Windows 11 本机；Python 3.14 与项目 venv 并存
- B 模块套件 **无需 PostgreSQL 可独立运行**：`pytest backend/tests/modules/alerts` → **154/154**（conftest 覆盖根 autouse PG fixture）
- 四项本地门禁全绿：ruff check + format、`mypy app`（strict）、`ty check app`（error-on-warning）
- 全仓库完整套件仍需 PostgreSQL（根 conftest autouse），B 本机未跑——CI 为权威
- 实际应用启动/登录/设备页联合检查：仍未做（待与 A 约定）

### 凭据管理（当前状态）

- git HTTPS 认证：Git Credential Manager（B 本人账号，已核验身份与私有仓库读写权限）
- 自动发帖：`scripts/gh_api_gcm.py` —— 运行时经 `git credential fill` 取认证，凭据仅存于进程内存，不进源码/Git/日志/输出
- 仓库内 `git grep` 实测：工作区与 HEAD **0 处**凭据字面量
- 曾泄露于发帖脚本的旧令牌已于 2026-10-13 由持有人撤销（API 实测 401 失效）；历史清理（filter-repo）涉及共享链，待队长确认，不擅动
- `.env.freshlink` 不入 Git；SMTP/推送凭据只在运行时环境注入

### B3 进度

| 子任务 | 状态 |
|---|---|
| B3-1 SMTP 分阶段分类（4yz/5yz、按收件人 outcome、NOOP 标注） | ✅ `b5aefce` |
| B3-2 F02 select_rules_for_observation + 重叠拒绝 + supersedes 闭窗 + DTO 严格化 | ✅ `c2c8995` |
| B3-3 F03/F04 接收持久化（五类回执 + by-source + content digest） | ⏳ 下一步（契约样例已在 #5 6139896658 定型） |
| B3-4 两种真实通知实收 | ⏳ 待接收人授权 |
| B3-5 alembic 迁移沿合并链（UniqueConstraint ×2 + append-only 触发器 + 新列） | ⏳ |
