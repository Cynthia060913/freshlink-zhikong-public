# A 方演示组件（5 页，可编辑）说明

本目录交付三件：`A-DEMO-COMPONENT.pptx`（最终成品）、`build-a-demo.mjs`
（可再生成脚本）和本说明。这是 A 方素材，供 B 汇编 M04 总 PPT 与 A 录屏，
**不取代 B 的总 PPT，不代表 M04 或完整 E1 签收**。

## 生成与运行方式

- 用 bundled Node（v24）+ `@oai/artifact-tool`（2.8.103）写成 ES module，
  未安装任何 npm/pip 包，未使用 python-pptx，未改全局环境。
- 字体统一 `Microsoft YaHei`（bundled 运行时的 204 个字体之一，含 CJK）。
- 再生成命令：在本仓库根，先由本机运行时清单确认并在当前进程设置
  `RUNTIME_NODE`、`RUNTIME_NODE_MODULES`、`RUNTIME_PYTHON`、`SKILL_DIR`。
  不在源码写入某台电脑的用户名或固定安装目录；不安装替代依赖。

```powershell
# 以上四项必须指向本人已存在的 bundled 运行时和演示技能目录。
# 已有成品保留，使用新文件名；finalizer 不覆盖既有 PPTX。
$env:A_DEMO_OUTPUT_NAME = "A-DEMO-COMPONENT-r2.pptx"
& $env:RUNTIME_NODE `
  docs/submission/claude-a-materials/build-a-demo.mjs
```

产物：新 PPTX 写入本目录，逐页 PNG 与 finalizer 报告写 `work/claude-a-materials/`。

## 五页内容与来源

| 页 | 标题 | 要点 | 关键源（相对路径 + 完整 SHA） |
|---|---|---|---|
| 1 | 冷库业务与角色范围 | 两库三批十四仿真设备、七测区、采集/执行身份；设备→观测→处置链；A/B/C 角色 | `docs/submission/A-SYSTEM-DESIGN.md`；设备层 `docs/modules/A/35-fourteen-device-runtime.md`，`8cd5f2dac57df94699b2e06e1c398320b5393eb5` |
| 2 | 共享系统架构与模块归属 | 复用现有架构图，标展示/HTTP/A 入口/B 规则告警/C 数据质量/存储分层 | `docs/submission/design/tikz_architecture.png`；`docs/submission/design/README.md`，业务源 `71e5a8820ee966db1b250afaf0a72143a4bdc62b` |
| 3 | 控制与数据链的分段证据 | 注册→在线→命令→ACK→后续温度→raw/清洗/历史/CSV；区分已分段证明与未完成 E1 | `docs/submission/A-TEST-REPORT-CANDIDATE.md`；`docs/modules/A/46-gated-fleet-storage-control.md`（`62bb2ef`）、`55-current-c-read-ports.md`（`ecf19d092075fa825e565d7f5f47c39519432ead`） |
| 4 | 可靠性与真实验证边界 | 一张简表：版本/实际证明/不代表什么（事务回滚、持久接纳、固定快照、合成派发重启） | `docs/modules/A/54-notification-runner-composition.md`（`1b7a9d91e6a8a426eb4995e7f9114bff1a399ba7`）、`55`、`46` |
| 5 | 验收与交付状态 | 已通过的候选能力 vs 待填（恢复端口/F03、两渠道实收、完整 E1、第二机、M01—M05） | `docs/submission/README.md`；分支业务源 `4dbf136bef902df3018ec322a0eeb18b9909a157` |

每页 speaker notes 记录了来源相对路径、完整源 SHA、对应真实证据与未测项，
未把全部 SHA 塞满版面。

## 检查结果

- finalizer 包结构检查：`packageIntegrity.status=pass`，0 finding，5 页，0 警告。
- 布局/字体检查：0 finding、0 警告；129 个文本 run 全部 `Microsoft YaHei`；
  页面 16:9（13.333×7.5 in）；原生表格在第 4 页（可编辑）。
- OOXML 文本扫描：邮箱/手机号/学号/令牌/机器人 hook/QQ 邮箱域名均 0 命中；
  全 5 页与备注为可编辑 `<a:t>` 文本（79 run），仅 1 张图片（现有架构图）。
- 逐页 PNG 已渲染（1280×720），像素覆盖 6.6%–19.2%，边缘无深色像素（无裁切）。
- 最终 PPTX SHA256：`3044ffdfc9b9f7036babd784bbf1ff763773f2be65686c86f9e5095ed6c0158f`。

## A 逐页视觉复核

Claude CLI 无法查看 PNG，曾如实交由 A 复核。2026-10-25 A 已实际逐页查看五张
1280×720 渲染图：正文、架构图和表格可读，未发现明显裁切、乱码或重叠；
可作为汇编素材。第4页表格信息较密，最终总PPT汇编时可按演示场景简化或拆页，
不为排版改写测试事实。本次未修改 PPTX 字节，以上 SHA256 保持有效。
这不是 PowerPoint 原生打开验收，也不是最终 M04 签收。

## 未测与限制

- 仅“候选 + 分段证据”立场，不含最终通过勾号、假截图或假收件。
- 图中组件存在不表示后台调度已启用；SMTP＋飞书两端实收、完整 E1、第二机、
  M01—M05 与实际投递均未签收，不在本材料内标注为完成。
