# E1 双渠道通知配置指南（B 按当前实际源码整理，供 A 在 A 本机操作）

适用提交：main `36cdc449d6f6b8bdb50b8cd0e74f2bacf97b4546`（dispatch/runner 与
`app/modules/iot/notification_service.py` 以此为准）。E1 在 **A 的电脑**运行，
所有秘密（SMTP 授权码、飞书签名密钥）由 **A 本人在 A 本机输入**；
B 不索取、不代填、不经聊天/GitHub/截图接收任何凭据。

源码事实依据（勿凭记忆改）：
- 环境读取：`backend/app/core/config.py` → `env_file="../.env"`（backend 目录
  上一层的 `.env`），OS 环境变量优先于 .env 文件。
- 渠道装配：`backend/app/modules/alerts/notifications/dispatch.py::build_transports`
  —— SMTP 在 `SMTP_HOST` 与 `EMAILS_FROM_EMAIL` **都非空**时装配；
  飞书在 `FEISHU_WEBHOOK_URL` 通过官方端点校验时装配；未配置/非法的渠道
  **静默跳过**（不报错），所以"渠道是否被识别"只能以服务输出的
  `configured_channels` 为准。
- 严格私有配置：`notification_service.py::NotificationServiceSettings`
  `extra="forbid"`，**只接受** `recipients` / `max_events_per_cycle` /
  `max_attempts_per_channel` / `poll_seconds` 四个键；多塞任何键（包括
  SMTP 密码、Webhook）整个配置解析直接失败。

---

## 1. SMTP 发件配置（发件通道）

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `SMTP_HOST` | 是 | 发件服务器，如 `smtp.qq.com` / `smtp.163.com` / `smtp.gmail.com` |
| `SMTP_PORT` | 否 | 默认 `587` |
| `SMTP_TLS` | 否 | 默认 `true`（STARTTLS，配合 587） |
| `SMTP_SSL` | 否 | 默认 `false`（465 隐式 SSL 的邮箱才设 `true` 并把端口改 465） |
| `SMTP_USER` | 通常=发件邮箱 | 登录账号（多数国内邮箱就是发件邮箱地址） |
| `SMTP_PASSWORD` | 是 | **SMTP 授权码**，不是网页登录密码（QQ/163 均需在邮箱设置里开通 SMTP 并生成授权码） |
| `EMAILS_FROM_EMAIL` | 是 | 发件人邮箱，必须与该邮箱账号一致；`SMTP_HOST`+它两者齐备才算"已配置" |
| `EMAILS_FROM_NAME` | 否 | 显示名，默认用 `PROJECT_NAME` |

注意事项：
- 授权码在邮箱服务商后台生成（QQ 邮箱：设置→账户→POP3/SMTP→生成授权码）。
- 端口/加密组合：587+STARTTLS 最常见；若服务商只给 465，用
  `SMTP_SSL=true`、`SMTP_PORT=465`、`SMTP_TLS=false`。
- 秘密只进环境变量或 A 本机的 `.env`，不进 Git、聊天、截图、日志。

## 2. 飞书自定义机器人 Webhook 配置（手机端实收通道）

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `FEISHU_WEBHOOK_URL` | 是 | 群里添加"自定义机器人"后得到的完整 hook 地址，必须是
  `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx` 官方端点；带 query/fragment、
  非官方域名都会被校验拒绝（该渠道被跳过） |
| `FEISHU_WEBHOOK_SECRET` | 建议是 | 机器人开启"签名校验"后给的签名密钥；留空则按无签名发送（机器人未开签名时可为空） |
| `FEISHU_WEBHOOK_TIMEOUT_S` | 否 | 请求超时秒数，默认值见 `feishu.py` |

- 手机端实收 = A 手机安装飞书并在被 @/收消息的群里开启通知。
- Webhook URL 与签名密钥都是凭据：只存在于 A 本机环境，不进任何公开材料。
- 飞书渠道在 `configured_channels` 里显示为 `app_push`（渠道枚举复用，
  见 `feishu.py` 的 `kind = NotificationKind.APP_PUSH`）——这是当前源码
  的既定事实，不是配置错误。

## 3. notification_service 私密收件人 JSON（严格模型，禁止放秘密）

`--config` 指向的 JSON **只接受这四个键**（`extra="forbid"`，多一个键整个启动失败）：

```json
{
  "recipients": ["<收件邮箱1>", "<收件邮箱2>"],
  "max_events_per_cycle": 1,
  "max_attempts_per_channel": 1,
  "poll_seconds": 60
}
```

- `recipients`：1–20 个合法邮箱（EmailStr）。**SMTP 渠道被识别但 recipients
  为空 → 直接拒绝启动**（`SMTP recipients required`）。E1 预检/受控发送
  只填 A 本人已同意接收的那 1 个邮箱。
- `max_events_per_cycle`：1–50，默认 1；`max_attempts_per_channel`：1–3，默认 1。
  **max_events=1 不代表一定选中"批准的那条事件"**——选中的是库内最旧的
  待派发事件，所以 E1 测试库必须清空到只剩批准事件。
- `poll_seconds`：1–3600。
- **不要**在这个 JSON 里放 SMTP 密码、Webhook、签名密钥——它们只走环境
  变量（第 1、2 节）；JSON 文件路径可放任意位置，内容无秘密，可留在
  本地不入库。

## 4. 怎么输入秘密（A 本机，不覆盖已有配置、不全局 setx）

优先方案（推荐，零文件改动）：在**本次命令行会话**里设置进程级环境变量，
关掉窗口即消失，完全不碰已有 `.env`：

- PowerShell：
  `$env:SMTP_HOST="..."; $env:SMTP_PASSWORD="..."; $env:EMAILS_FROM_EMAIL="..."; $env:FEISHU_WEBHOOK_URL="..."; $env:FEISHU_WEBHOOK_SECRET="..."; $env:DATABASE_URL="postgresql+psycopg://<E1测试库连接串>"`
  然后在**同一个窗口**启动服务。
- 禁止 `setx`（写入全局注册表，属全局修改）；禁止改宿主 Python 环境。

备选方案：编辑仓库顶层 `.env`（backend 的上一层）追加/修改上述键。
**若 A 已有真实 `.env`，只追加/修改列出的键，不要整文件覆盖**；
`.env` 已在 `.gitignore` 中，确认不要 `git add`。

## 5. 启动与输出怎么读

在 `backend/` 目录、A 的虚拟环境里：

```
python -m app.modules.iot.notification_service --config <私密JSON路径> --max-cycles 1
```

- **默认 dry-run**（没有 `--send` 就绝不发送、绝不写任何尝试行）。
- 输出一行 JSON：
  `dry_run / cycles / configured_channels / pending_count / interrupted /
  terminal_receipt_confirmed`
  - 双渠道都识别 ⇒ `configured_channels` 为 `["smtp_email", "app_push"]`
    （第二个是飞书，见第 2 节说明）。
  - 退出码：0 正常、1 中断/派发异常、2 配置拒绝或数据库不可用。
- `terminal_receipt_confirmed` 恒为 `False`：**服务接受 ≠ 手机/邮箱实收**；
  实收以 A 手机/邮箱亲眼确认为准（受控发送后由 A 确认并记录）。
