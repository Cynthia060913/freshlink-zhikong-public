# B → A 集成接线说明（integration-notes）

> 落地位置：`docs/modules/B/integration-notes.md`（共同仓库 `feature/b-notification-bootstrap` 分支）。
> 目的：明确 B 第一轮**没动**共享根路由/导航/Compose/锁/迁移；列出 B 在 B1/B2 阶段需要 A 接力的接线动作。

## 0. 范围

本文件只列出 B 第一轮**未做、但被 B1/B2 阻塞**的公共根/集成动作。
B 第一轮已完成的局部实现见 `01-contract-proposal.md` 与各模块内的 docstring。

## 1. 已做（无需 A 接力）

| 项 | 路径 | 状态 |
|---|---|---|
| 模块目录 | `backend/app/modules/alerts/` | 已建 |
| 子模块 | `backend/app/modules/alerts/notifications/` | 已建 |
| 单元测试 | `backend/tests/modules/alerts/` | 已建；4 类 SMTP + APP 全绿 |
| B 文档 | `docs/modules/B/00-readiness.md`、`01-contract-proposal.md`、`integration-notes.md`（本文） | 已建 |

## 2. 待 A 接力的根动作（B 不写，请 A 在 A 接力回合做）

### 2.1 公共根路由 register

`backend/app/api/main.py` 当前只导入 `iot`、`items`、`login`、`users`、`utils`、`private`。
B1 阶段 B 会写 `backend/app/modules/alerts/routes.py`；A 需要：

```python
from app.modules.alerts.routes import router as alerts_router

api_router.include_router(alerts_router)
```

加在 `backend/app/api/main.py` 的 `api_router.include_router(...)` 调用之后。

理由：v4 第 5 章明确"公共根路由/模型注册/导航/Compose/依赖锁/契约属于共享区：每次指定一个 Agent 处理，其他人写 `integration-notes.md` 给出所需接线，不同时修改"。

### 2.2 公共根路由 navigation entry

`frontend/src/components/Sidebar/AppSidebar.tsx` 当前包含 `devices` 等若干条目。
B2 阶段建议 A 在 Sidebar 增加 `alerts` 与 `warehouses` 条目（指向 `/alerts` 和 `/warehouses`）；B 的 `frontend/src/features/{alerts,warehouse}/` 在 B1/B2 阶段建。

理由：同上（共享导航属于 A 主导）。

### 2.3 alembic 迁移链

本轮**未生成** alembic 迁移。B1 阶段 B 的 `warehouse` 与 `alerts` 模型需 A 协调：

- 迁移以已合并的当前 head 为准（v4 第 5 章）
- `alembic upgrade head` 须在 A 那台 Docker 已起的环境验证空库升级
- 不删 A 的迁移；B 的迁移接 a01_device_registry 之后

### 2.4 INTERFACES 字段冻结

`contracts/INTERFACES.md` v0.1 仍为草案。A 本对话 Agent 收到 B/C 三方提案后做：

1. 合并三方 `01-contract-proposal.md`
2. 版本化为 v0.2 或更高，加版本号与提交号
3. 共同基线对齐后通知 B / C

B 在 v0.2 之前的迁移都不算正式固化。

### 2.5 Issue 创建与远程追踪

`docs/team/v4/issues/B-主任务.md` 是本机副本。A 用户在 GitHub 网页创建 Issue 后贴真实 `#num`。B 在 PR 描述里 `Refs #<num>`，但**不**用 `Closes #<num>`（避免整块主任务被自动关闭）。

## 3. B 第一轮不会做（确认无歧义）

- 不改 `backend/app/api/main.py`
- 不改 `frontend/src/components/Sidebar/AppSidebar.tsx`
- 不改 `compose*.yml`、`pyproject.toml`、`uv.lock`、`bun.lock`、`package.json`
- 不写 alembic 迁移
- 不改 `contracts/INTERFACES.md` 字段
- 不在远程创建 Issue / PR / 评论
- 不 push
- 不 merge main
- 不安装 docker / wsl
- 不发任何外部通知（无授权）

## 4. A 接力回收标记

如果 A 接力整合本分支，至少需要补：

1. `backend/app/api/main.py` 一行 `include_router`（B 阶段二后）
2. alembic 迁移顺序协调
3. INTERFACES 版本化提交（如未做）
4. （可选）引入 client 自动生成的接口定义文件到 B 模块（按 v4 第 5 章"路由文件可以按模板生成"）

A 实际整合后，B 模块才能进一步落地 service.py / routes.py。

## 5. F03-RC2 候选输入契约（2026-10-14，PR #10 四缺陷修复）

本节为准线状态，覆盖第 2/3 节中与新实现冲突的旧描述。修复 A 在新库 #8 复现的四项
缺陷后，`POST /candidates` 的输入按 `contracts/F03-RC2.md`（feature/a-b-integration-review
分支）执行，与 `contracts/examples/candidate-f03-rc2.json` 对齐：

- 新增字段：`schema_version`（仅接受 `"1.0"`）、`warehouse_id`（必填）、
  `observation_ids`（≤64 个，各 1–128 字符）、`severity`（必填，1–32 字符）、
  `rule_snapshot_id`、`rule_version`、`algorithm_version`、`evidence`（任意 JSON 对象）。
- `sampled_at` 同时接受 ISO-8601 字符串（`Z`/带偏移）与内部 aware datetime；naive 一律 422 拒绝，不猜测时区。
- 规范摘要（content digest）覆盖全部新字段：同 `source_event_id` 换任何追溯证据重投 → `conflict(409)`，不再被忽略后误报 `accepted_duplicate`。
- 校验：批次必须属于候选声明的 `warehouse_id`，否则 `invalid(422) reason_code=batch_warehouse_mismatch`；规范化负载 >32768 字节显式 422，不截断。
- 存储与回读：`alerts_event` 新列 `severity/observation_ids_json/rule_snapshot_id/rule_version/algorithm_version/evidence_json`；`GET /candidates/by-source/{sid}` 返回完整追溯正文（缺陷 2 的回读要求）。迁移（alembic）待接已合并唯一 A→B→C 链时补。
- 存储故障分类（缺陷 3）：前置读取、幂等查找、并发冲突后回读任一 `OperationalError` → `retryable_failure(503, Retry-After: 30, reason=storage_unavailable)`；by-source 读取故障同映射 503，`not_found(404)` 语义不变。
- 规则版本替换（缺陷 4）：写侧重叠检查改用与读取端相同的后继派生半开窗口，开放链 S1→S2→S3 的第三次替换不再误判重叠；乱序或未声明的真实重叠仍拒绝。

## 6. B2 告警生命周期状态与 A 接力项（2026-10-20，PR #10 `2a41929`）

### 6.1 已做（B 模块内，已推 PR #10 分支）
- 路由：`GET /alerts/{alert_event_id}/status`（任意活跃用户）；
  `POST /alerts/{alert_event_id}/acknowledge`（**仅超级用户**，open→acknowledged，
  幂等重复不新增迁移行，resolved 409 零写入）。
- 服务：`acknowledge_alert`；DTO：`AlertLifecyclePublic`、
  `AlertStatusTransitionPublic`；聚合于 `app/modules/alerts/routes/__init__.py`
  的 aggregate（不加任何全局前缀）。
- 测试：`backend/tests/modules/alerts/test_lifecycle.py` 6 项 HTTP 回归。

### 6.2 仍需 A 接力的共享根动作
1. `backend/app/api/main.py` 增加一行（挂在 `device_control_router` 之后即可）：
   ```python
   from app.modules.alerts.routes import router as alerts_router

   api_router.include_router(alerts_router)
   ```
   挂载后 `/api/v1` 前缀由既有外层加一次；OpenAPI 将变化，需同步
   `scripts/generate-client.sh` 重新生成 `frontend/openapi.json` 与 SDK
   （B 本地无 bun 时无法生成，故挂载 PR 由 A 执行并附生成产物最稳）。
2. 导航：`frontend/src/components/Sidebar/AppSidebar.tsx` 增 `alerts`
   条目（指向 `/alerts`），权限与设备页一致（登录可见）。
3. 前端处置页面：B 将在 `frontend/src/features/alerts/` 开发列表/详情/确认；
   所需路由文件位置请 A 在根路由候选（PR14）中预留 `/alerts` 路径或告知
   B 可直接新增 `frontend/src/routes/_layout/alerts.tsx`。

### 6.3 权限约定（已实现，供 A 接根时核对）
- 读：任何活跃用户；确认：`get_current_active_superuser`；普通账号确认返回
  403 且零写入（与 A 的"普通账号只读且实际 POST 403"一致）。
