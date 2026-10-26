# E1 零发送预检清单（B 整理，默认 dry-run，不使用 --send）

前提：A 已按 [B-E1-CONFIG-GUIDE.md](B-E1-CONFIG-GUIDE.md) 在 A 本机准备好
环境变量（本次会话级）与私密收件人 JSON。**本预检全程不使用 `--send`，
不产生任何真实消息、任何网络外呼、任何数据库写入。**

明确边界：**dry-run 通过不证明 SMTP/飞书服务可连接，也不证明手机/邮箱
会实收**；它只证明配置可解析、两个渠道被识别、连的是指定测试库、派发
扫描能列出待发事件。

## 0. 源码与库

- 预检针对的源码：main `36cdc449d6f6b8bdb50b8cd0e74f2bacf97b4546` 起的
  派发层（或 A 指定的 E1 检出 SHA，运行前记录精确 SHA）。
- E1 专属测试库：`DATABASE_URL` 指向本次专用 PostgreSQL 库（如
  `postgresql+psycopg://<user>:<pwd>@127.0.0.1:<port>/<e1test>`，
  用 `127.0.0.1` 避免 localhost/IPv6 解析拖慢连接）。
- **库内不得混有其他待发送事件**（见第 3 步核对）。

## 1. 配置可解析（不含秘密侧）

```
python -m app.modules.iot.notification_service --config <私密JSON路径> --max-cycles 1
```

- 通过 = 退出码 0/1 且输出一行 JSON；解析失败（多键/类型错）= 退出码 2。
- 本步同时验证严格模型：JSON 里若误塞了 SMTP 密码/Webhook 会在这里
  直接失败——这是设计行为。

## 2. 两渠道均被识别

同一命令的输出 JSON 里：

```json
"configured_channels": ["smtp_email", "app_push"]
```

- `smtp_email` = SMTP；`app_push` = **飞书**（渠道枚举复用，源码既定）。
- 缺 `smtp_email` ⇒ 检查 `SMTP_HOST` 与 `EMAILS_FROM_EMAIL` 是否都非空
  （两者齐备才装配）。
- 缺 `app_push` ⇒ 检查 `FEISHU_WEBHOOK_URL` 是否为官方
  `https://open.feishu.cn/open-apis/bot/v2/hook/...` 且无 query/fragment。
- 同时确认输出 `"dry_run": true`（未加 `--send` 必为 true）。

## 3. 连接的是专属测试库（三重核对）

1. 输出 JSON 的 `pending_count` 与该库应有待发事件数一致。
2. 只读核对（psql 或只读会话）：
   ```sql
   SELECT source_event_id, kind, status, created_at
   FROM alerts_event ORDER BY created_at;
   ```
   结果应**只包含**批准的合成事件（受控发送阶段只留 1 条）。
3. 确认命令所在窗口的 `DATABASE_URL` 值是 E1 测试库连接串
   （PowerShell：`$env:DATABASE_URL`——仅本地查看，不截图发布）。

## 4. dry-run 的行为语义（runner.py 源码语义）

- `dry_run=True` 周期**只报告**将要派发的事件（`pending` 列表），
  **不发送、不写尝试行、不取租约之外的状态变更**。
- `--max-cycles 1` 跑一个周期即退出；`terminal_receipt_confirmed` 恒
  `false`——真实实收只能在受控发送后由 A 在手机/邮箱确认。
- 若 `pending_count` 为 0 而库里应有事件：检查事件是否缺通知目标
  字段/已被派发过（attempt 预算表），不要靠改库硬凑。

## 5. 预检通过后

停在 dry-run。向 A 汇报：精确 SHA、`configured_channels`、
`pending_count`、测试库核对结果。真实发送须走
[B-E1-REAL-SEND-REQUEST.md](B-E1-REAL-SEND-REQUEST.md) 的具体批准流程，
**预检通过不是发送许可**。
