# 08 一次性交接（八文件、四项提交、来源与待补）

软件来源 `bfb2c927f2a1a083ac21996844d38ac4eb4abbef`（A PR82 集成候选，非已发布 main）。
本文件是四项连续交付的一次性交接；四项完成即停止，等 A 逐项复核。最终发送只由本人或具体
批准后执行；不生成看似已通过的验收记录，不发邮件/飞书、不提交参赛。

## 1. 八个文件与四项提交

| 文件（`docs/submission/claude-final-queue/`） | 对应任务 | 提交（完整 SHA 以 `git log`/PR 正文为准） |
|---|---|---|
| `01-REQUIREMENTS-AND-DELIVERABLES.md` | 任务一 | c53c836 |
| `02-VERIFIED-TEST-CHAPTER.md` | 任务一 | c53c836 |
| `03-USER-OPERATION-CARDS.md` | 任务二 | e1a2d5a |
| `04-RUN-AND-SECOND-MACHINE.md` | 任务二 | e1a2d5a |
| `05-DEMO-RUNSHEET.md` | 任务三 | 33f2510 |
| `06-NARRATION-DRAFT.md` | 任务三 | 33f2510 |
| `07-PACKAGE-REVIEW.md` | 任务四 | 本提交（07+08 同一次） |
| `08-HANDOVER.md` | 任务四 | 本提交（07+08 同一次） |

## 2. 实际检查（本轮真实做过）

- 已读 `AGENTS.md`、`docs/team/PUBLIC-MIGRATION.md`、`DELIVERY-CHECKPOINTS.md`、
  `FINAL-WEEK-PLAN.md`、`docs/team/v4/09-完整目标与参赛验收矩阵.md`、`docs/submission/README.md`。
- 已读 `A-SYSTEM-DESIGN.md`、`A-TEST-REPORT-CANDIDATE.md`、`APPLICATION-CANDIDATE-BUILD.md`、
  `SOURCE-CANDIDATE-EXPORT.md`、`design/README.md`、`docs/modules/A/62-linked-correction-runtime.md`。
- 已读前端源码 `frontend/src/features/{devices,telemetry,alerts}/` 与 `frontend/src/routes/*`、
  `routeTree.gen.ts`、`components/Sidebar/AppSidebar.tsx`，按钮/字段/路由名与卡片一致。
- 已读 `scripts/windows/{check-environment,start-local,stop-local,smoke-test,export-source-candidate,
  build-application-candidate,check-application-candidate,common}.ps1`，命令/参数与 04/07 一致。
- 已实际读取本任务书五组证据：G1 `runtime-composition.json`、G2 `runtime-child-cleanup.json`/`cleanup.json`、
  G3 `current-c-b83.xml`、G4 `application-build.json`、G5 `application-http.json`/`image-cleanup.json`。
- 已 `git fetch` 取得 PR69/70/71/77 对应分支（84232d1、b9d0327、6d32984、9e9ba3a）并读取其
  Markdown，复用运行说明/录屏脚本/包说明/技术章节与讲稿；`.pptx`、`.webm` 二进制未读内容，仅按文档引用。
- 核对相对 bfb 的改动仅为本任务书目录下 TASK.md（只读，未改）与八个新文档。

## 3. 未读 / 未取得 / 待核对

- 任务书证据根目录下**其他**文件/日志/真实配置按任务书要求**未扫描**，只读列出的五组证据路径。
- G3 差异已由 A 核清：终端和实际 testcase 均为 69，XML 汇总属性为 72；同版本 pytest
  的纯合成 subTest 探针复现了汇总额外计数。详细口径见 02，不改原始证据。
- PR11 是否已合并、共同 main 最新 SHA、E1/两通知/第二机最新进展未回读（网络受限仅 ls-remote 成功）；
  本材料以 bfb 候选为源，不代表这些已通过。

## 4. 待 A/B/C 补齐（不称成品）

以下为 bfb 软件来源时的缺口快照。2026-10-26 晚 C 两处正式修复、B 新头与当前组合
120 项/1433 项及十四设备实跑已由 A 追加核验，见 `../A64-INTEGRATION-CHECKPOINT.md`。
这些新结果不改变本材料原始来源，也不代表完整 E1/通知/第二机/最终材料已经签收。

- A：bfb 级在线/离线、14 设备、浏览器同版实跑；无人值守恢复发送；第二机复现；冻结版重反射/重出图。
- B：SMTP + 飞书两渠道授权与终端实收；告警持久派发/通知 attempt 接线；M03 汇编与 M04 总 PPT。
- C：多批 revision-only 续验与保存原文一致性正式修复；真实测试/数据章节；第二机支持。

## 5. "只差填真实结果"的明确位置

- `04-RUN-AND-SECOND-MACHINE.md` §4：第二机验收记录表（由真正另一台电脑的执行者本人填）。
- `05-DEMO-RUNSHEET.md` 镜头 6/7：恢复追溯、两通知实收，待验证/待实收后录制。
- `07-PACKAGE-REVIEW.md` §5：解包字节数/SHA256/页数/时长，冻结版实测后填。
- `07-PACKAGE-REVIEW.md` §1：包名里的 `赛道号_学号_姓名_项目名称` 身份由本人填。
- `02-VERIFIED-TEST-CHAPTER.md`：不含最终通过结论，最终 M03 由三人按冻结版逐项签收后填。

## 6. 停止边界

四项已完成，停止修改，等 A 复核。不发送邮件/飞书、不提交参赛、不面向 main 合并；本包完成
不代表业务项目或 E1 完成。
