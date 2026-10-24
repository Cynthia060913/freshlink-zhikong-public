# 材料交接（M01—M05 映射，不是签收）

依据固定提交 `96afaab8ef5a26725a0fa31ff4ff3fc4983698bf`。逐项列当前可定位的文件或候选来源、
负责人、尚缺内容、下一步如何证明完成。状态四档严格区分：**文件存在 / 候选底稿 / 实际运行 /
最终签收**。不编造文件大小、哈希、截图、测试或提交凭证。

## 状态总览

| ID | 交付物 | 负责人 | 当前可定位 | 状态 |
|---|---|---|---|---|
| M01 | 源码 | A；三人供模块 | `SOURCE-CANDIDATE-EXPORT.md` + `export-source-candidate.ps1` | 候选底稿（工具就绪） |
| M02 | 可运行应用 | A；B/C 供依赖说明 | `APPLICATION-CANDIDATE-BUILD.md` + `build/check-application-candidate.ps1` | 候选底稿 |
| M03 | 设计/测试报告 | B 文档、A 架构 ER、C 测试 | `A-SYSTEM-DESIGN.md`、`A-TEST-REPORT-CANDIDATE.md` | 候选底稿 |
| M04 | 可编辑 PPT | B；A/C 供素材 | 未定位成品 | 未签收 |
| M05 | 视频 | A；B/C 供短录屏 | `DEMO-SCRIPT.md`（`claude/demo-script` 分支） | 脚本候选，成品未录 |

## M01 源码

- 可定位：`docs/submission/SOURCE-CANDIDATE-EXPORT.md`；`scripts/windows/export-source-candidate.ps1`
  `-Revision <冻结40位SHA>` 生成源码 ZIP + `manifest.json`（`work/source-candidates/`）。
- 尚缺：冻结版本未定；逐字节核对与秘密/许可证人工复核未完成。
- 证明完成：ZIP 与同版 SHA 一致、可解包、关键注释/依赖锁/LICENSE/来源齐全、无秘密/私有日志/数据库。
- 当前状态：**候选底稿**，不是最终源码包。

## M02 可运行应用

- 可定位：`docs/submission/APPLICATION-CANDIDATE-BUILD.md`；`build-application-candidate.ps1`
  `-Revision <SHA>` 产出唯一镜像，`check-application-candidate.ps1 -BuildReport <json>` 做默认启动检查。
- 尚缺：镜像默认 CMD 实际启动、空库/已有数据升级、A/B/C worker 接线、第二机从交付物启动。
- 证明完成：另一电脑从交付物启动并实跑普通操作，不依赖 A 私有缓存/路径。
- 当前状态：**候选底稿**；不能把镜像标签或构建成功当“可运行签收”。

## M03 设计文档与真实测试

- 可定位：`A-SYSTEM-DESIGN.md`（需求/架构/ER 底稿）、`A-TEST-REPORT-CANDIDATE.md`（测试候选章节）。
- 负责人：B 汇编需求/方案/结构正文，A 供架构与 ER，C 供真实测试/数据章节。
- 尚缺：三章合一、图按冻结版实际迁移/部署核对、测试逐条记录版本/环境/对象/步骤/实际/证据/缺陷复测。
- 证明完成：三人核各自章节；Mock、服务接受、终端实收、跨模块实链分开记录；不列未实现为完成。
- 当前状态：**候选底稿**，不是完整 M03。

## M04 可编辑 PPT

- 负责人：B；A/C 各供 4 页素材/来源/讲稿。当前仓库内未定位最终成品。
- 尚缺：PPT 成品、可编辑稿、截图/数字/主张可追溯到同版发布。
- 证明完成：打开可编辑、内容可追溯、无夸大主张。
- 当前状态：**未签收**；本任务不替 B 制作或覆盖 PPT。

## M05 视频

- 负责人：A；B/C 供实际短录屏/实收。`docs/submission/claude/DEMO-SCRIPT.md`（`claude/demo-script` 分支）
  是预排脚本，不是成品。
- 尚缺：同版实拍、完整主链/控制后数据变化/追溯、两通知实收镜头、另一电脑完整播放可懂。
- 证明完成：视频可播放、声音可懂、覆盖必选要求、无虚构结果。
- 当前状态：**脚本候选，成品未录**。

## 共同收尾要求

- GitHub 上传**不是参赛投递**：压缩包必须同版、可解包、文档可打开、视频可播放。
- 需要最终生成的数据（字节数、SHA256、页数、时长）先写**测量方式**，不先填假值。
- 最终由 A 统一核验版本与材料，逐项对齐 `docs/submission/README.md` 签收表；实际参赛发送由本人
  操作或取得具体授权后发送，本文件不授权对外投递。
