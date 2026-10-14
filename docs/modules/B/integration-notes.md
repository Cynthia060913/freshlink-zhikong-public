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
