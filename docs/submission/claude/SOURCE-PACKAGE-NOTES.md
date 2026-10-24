# 源码包说明（M01 准备，不是最终发布）

依据固定提交 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`。帮助 A 整理 M01，说清“交付源码、
可运行应用、数据库初始化、私有配置样例”四者的关系，减少漏文件、混版本、把候选当成品。
本页不实际打最终包，不复制本机数据库或私密配置，不宣称完成全量许可证审计。

## 1. 四者关系

| 物 | 是什么 | 谁产生 | 交付时 |
|---|---|---|---|
| 交付源码 | 固定版本的全部已跟踪文件 | `scripts/windows/export-source-candidate.ps1 -Revision <40位SHA>` | 源码 ZIP + `manifest.json` |
| 可运行应用 | 从源码构建的镜像 | `scripts/windows/build-application-candidate.ps1 -Revision <SHA>` | 镜像 ID + 构建报告 |
| 数据库初始化 | 迁移链 + 空库起点 | 镜像内 prestart/迁移 | 由第二机用**自己的**空库执行，不分发数据 |
| 私有配置样例 | 无秘密的模板 | 上游根 `.env` 与 `frontend/.env`（已审核 blob） | 只交模板，真实值由使用者本地 `.env.freshlink` 生成 |

## 2. 必须保留的关键文件

- 依赖锁：`uv.lock`（后端 Python）、`bun.lock`（前端）——保留，无说明不得升级。
- 来源：`docs/UPSTREAM.md`（上游 FastAPI 模板、固定提交 `7257e606cb94eada1dca40173e2b7f99dd549bc6`）。
- 许可证：`LICENSE`（MIT，© Sebastián Ramírez）——**保持原样，不改动**。

## 3. 明确排除（绝不进包）

`work/`、真实环境配置（`.env.freshlink`）、数据库（`.db/.sqlite/.sqlite3`）、缓存
（`__pycache__`、`node_modules`）、密钥（`id_rsa/id_ed25519`、`.key`）、个人资料与凭据。
导出脚本会拒绝这些路径，`check-publication-safety.py --staged` 只做有限文本模式检查，
**不是完整隐私/安全/许可证审计**。

## 4. 团队新增 vs 上游模板 vs 第三方

| 类别 | 内容 | 可核对来源 |
|---|---|---|
| 上游模板 | FastAPI 全栈模板、认证/用户/items 示例 | `docs/UPSTREAM.md`，MIT LICENSE |
| 团队新增 | `AGENTS.md`、`START_HERE.md`、`docs/`、`contracts/`、`scripts/windows/`、`compose.freshlink.yml` | 本仓库对应文件 |
| 第三方组件 | Python/Bun 依赖，版本见 `uv.lock`/`bun.lock` | 各锁文件记录来源；**不逐个猜测许可证** |

## 5. 交付边界

- 源码 ZIP 状态只是 `verified_source_candidate_not_final_submission`（逐字节核对 Git 树），
  不等于 M01 签收、可运行、可公开或已完成安全/许可证审计。
- 依赖锁是来源声明，不是跨日期 bit-for-bit 复现承诺；构建成功时另记实际镜像 digest。
- 最终发布仍需：对齐冻结版 SHA、E1/完整场景、第二机复现、M01—M05 逐项签收、人工复核秘密与许可证。
