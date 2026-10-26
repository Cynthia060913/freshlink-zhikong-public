# 材料收尾队列统一交接（A 复核前停止）

本目录是 A 委托外部材料助手连续完成的四项交付。**只改 `docs/submission/claude-final-materials/`**
与本地 `work/claude-final-materials/`，未改业务代码、契约、迁移、依赖锁、共享启动脚本或
其他材料。四项完成即停止，等 A 逐项复核；本包完成不代表业务项目或完整 E1 完成。

## 1. 分支与软件来源

- 工作分支：`claude/final-materials-assembly`。
- 固定软件来源：main0e `0e508d147517f81dfe2b9245c45c0c0e30076592`（PR82/88 已合并）。
  本次材料只基于该 SHA，不把后来 main 自动混入，也不把该 SHA 当成完整 E1 已验收。
- 原素材来源：旧 PR77 `9e9ba3a6ef7c4d7db0c83ac98d7caaf8296e202d` 的
  `docs/submission/claude-a-materials/`（只读 `git archive` 提取 `build-a-demo.mjs`、
  `A-TECHNICAL-CHAPTER.md`、`CLAIM-SOURCE-MAP.md` 等）；主线
  `docs/submission/A-SYSTEM-DESIGN.md`、`A-TEST-REPORT-CANDIDATE.md`、
  `docs/submission/design/`；A 私有运行证据白名单（7 项，见任务书第 2 节）。

## 2. 四项交付与每文件哈希

| 任务 | 文件 | 字节 | SHA256 |
|---|---|---|---|
| 一 | `A-FINAL-COMPONENT.pptx` | 515945 | `28f16cfb23f0bfd86f7c9b58c38c879981442bfa7a569abf697024456aeb7def` |
| 一 | `build-a-final.mjs` | 20904 | `75ff67e642866e76e0fefc671852d6ab25b90b188a18d3c6d671062691160275` |
| 一 | `A-SLIDE-SOURCES.md` | 5824 | `b2b6ecb14b79ccc9fd6e781ff13b2ecdfa350284439bea89ec9783467c4024dc` |
| 二 | `A-TECHNICAL-APPENDIX.docx` | 43112 | `5b84fba032453b3790f3b6fbf101d4ae589ad23f5e0e8de863d3c550003c3bbd` |
| 二 | `A-TECHNICAL-APPENDIX.md` | 9172 | `69d32351668b95d64160831a49ee887c0121da741cea7eb69d66ed1f6c9f1591` |
| 三 | `build-material-index.py` | 9841 | `dd09f83f0a85a661034e6abaab1235466cd6b6fa4319b7303439d5f218b4e202` |
| 三 | `test-material-index.py` | 4297 | `7390afc0d453ae1f5018434375a32a19106a41ccb1cbf99cf3c1e7c5a6568dfe` |
| 三 | `material-files.example.json` | 647 | `7e23980d05580bdf31dedf2e401b280e2d0adb1df87c410def074de3ab70867f` |
| 四 | `MATERIAL-INDEX.html` | 见 MANIFEST.sha256 | 由 `build-material-index.py` 生成；自身 hash 见本地小包 `MANIFEST.sha256` |
| 四 | `FINAL-MATERIALS-HANDOVER.md` | 见 MANIFEST.sha256 | 本交接文件 hash 由最终 `MATERIAL-INDEX.html` 与 `MANIFEST.sha256` 记录 |

提交（完整 SHA 以 `git log`/PR 正文为准）：任务一 `19a54cc`、任务二 `754d767`、
任务三 `cdcda8c`，任务四为本提交（含本交接与更新后的 `MATERIAL-INDEX.html`）。

## 3. 实际检查（本轮真实做过）

- PPTX：`build-a-final.mjs` 实跑，finalizer 全绿——package integrity 0 finding、
  layout geometry 0 finding/0 warning、字体 Microsoft YaHei、可再导入、1 张原生表格、
  3 张真实嵌入图、每页讲稿；逐页 PNG 已导出到 `work/claude-final-materials/png/`
  与 `deck-montage.webp`。
- DOCX：bundled python-docx 1.2.0 由单一 `.md` 生成，A4 纵向、微软雅黑、黑字标题、
  11pt 正文、1 张可编辑证据表；python-docx 回读确认 81 段、6 标题、1 表。
- 索引工具：4 项标准库测试实跑通过（含中文+空格+hash、缺失非零仍写报告、越界/盘符/
  UNC 拒绝、HTML 转义）。
- 安全：每项提交前跑 `scripts/check-publication-safety.py --staged`，均 passed、无 findings。

## 4. 渲染与视觉状态（诚实边界）

- PPTX 逐页 PNG 已生成；**本会话视图工具未能显示这些 PNG，未做逐页人工像素视觉复核**，
  结构/几何由 finalizer 自动检查作为“无溢出/重叠”证据，不以像素检查冒充人工视觉通过。
  最终请在 PowerPoint 打开复核。
- DOCX 未做页面渲染：文档 skill 的 `render_docx.py` 依赖 LibreOffice `soffice.exe`，
  本机缺失（`FileNotFoundError: LibreOffice soffice.exe was not found on PATH`），
  按任务书不安装依赖，**DOCX 标记“渲染未验收”**，仅保留可编辑稿与结构检查。

## 5. 未测 / 真实缺口（不写成通过）

- 两通知（SMTP＋飞书手机端）授权实收、同一冻结版本完整 E1、最终同版第二机、C 自动恢复
  与多批高温业务、M01—M05 最终汇编与实际参赛投递，均未签收。
- 高温三批候选是鉴权 ASGI HTTP 到 B（非 TCP）；真实 TCP 恢复证据在受保护恢复后台单独记录。
- `notifications_sent=0`、`complete_e1=false`。控制实测降温是短时软件仿真，非实物制冷。

## 6. 供 B 汇编的定位（不是最终 M03/M04/M05）

本目录只交付 A 组件与附件：`A-FINAL-COMPONENT.pptx` 是 M04 的 A 素材组件（可编辑、带来源
表），`A-TECHNICAL-APPENDIX.md/.docx` 是 M03 的 A 技术章节。二者不是最终 M03/M04/M05 成品，
也不因本次任务完成而宣称整个项目或 E1 完成。最终 M01—M05 仍按 `docs/submission/README.md`
逐项签收。

## 7. 停止边界

四项已完成，停止修改，等 A 视觉/内容/代码复核与独立审查。不发送邮件/飞书、不部署、不安装
环境、不改原数据库、不 merge/强推、不面向 main 直接合入、不进入第五项。
