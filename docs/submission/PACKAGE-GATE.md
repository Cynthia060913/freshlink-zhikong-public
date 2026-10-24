# 最终交付包一致性检查（只读，不代人验收）

本工具只帮助在最终收口时阻止漏件/混版/哈希不符、漏掉实收和第二机的**记录**。
它不运行应用、不发邮件、不安装、不改数据库，也不把人工填写的 `passed` 证明为真。
没有实际验收不能为了让脚本变绿而填通过。通过输出明确为“清单/记录一致，不是最终验收”。

## 包目录与清单

最终材料目录由队长私下汇编，不把真实收件/报名信息或待脱敏日志推入公开 Git。
人工检查后，在根放 `submission-manifest.json`，结构如下；这是候选示例，必然拒绝通过：

```json
{
  "format": "freshlink-submission-v1",
  "source": "待最终冻结的40位小写Git SHA",
  "state": "candidate",
  "algorithm_enabled": false,
  "files": [],
  "acceptance": {}
}
```

文件条目：`role`、相对 `path`（使用 `/`）、同版 `source`、实际正整数 `bytes`、实际
64位小写 `sha256`。不是把现在的分段证据重新贴为最终版本；重新冻结后必须实测并记录。
至少包含 `source_archive`（ZIP）、`application_archive`（tar/zip/gz）、`runbook`、
`design_document`、`presentation`（可编辑PPTX）、`video`（MP4/WebM）；其他可列
`evidence` 或 `supporting`。每个实际文件都要入清单，不能只列最漂亮的几份。

`acceptance` 必须有 S0、E1、FLEET、SECOND_MACHINE、SMTP_RECEIPT、FEISHU_RECEIPT、
M01—M05、PRIVACY、SOURCE_LICENSES。每项包含 `state`、同版 `source`、`reviewer`
（A/B/C/captain）及非空 `evidence` 相对文件路径列表。通知另需
`consent_confirmed`、`terminal_received_confirmed` 为真实确认的布尔值；第二机另需
`independent_computer_confirmed`。最终准备好供本人复核时顶层状态才写
`ready_for_human_final_review`；它仍不表示已参赛投递。

启用可选算法时，本版脚本会阻止“最小无算法包”通过，须另补 M06—M08 的完整审查与
相应门禁，不可直接把算法标志改为 false 掩盖已宣传或已采用的算法。

## 执行与人工出口

在已有隔离 Python 3.11+ 或容器中，从实际项目目录执行（不安装/同步依赖）：

```text
python -I -B scripts/check-submission-package.py <实际材料目录>
```

只读计算每个文件的字节数/流式哈希、检查完整角色与同版本签收记录、路径逃逸/符号链接、
额外未列文件及明显配置/数据库/缓存路径。失败仅输出固定错误码，不打印文件内容或秘密。
纯一致性通过后仍要逐件打开文档/PPT、完整播放视频、解包源码/镜像和复现运行、检查
归档内部与图片/视频隐私和许可证，人工核对通知终端实收及真正第二电脑，不能省略。
同一份记录/空白文件可以被恶意包装得格式正确，本脚本不是鉴伪、签名或二进制安全审计。

本轮测试全部使用明确标注的合成假文件，正向只证明结构一致，负向证明缺件/混版等
会阻断。没有拿这些合成文件做参赛材料或验收证据。实际 A48 离线候选目录也不能通过
该最终包门禁，缺失项必须保留到实际完成，而不是扩展脚本白名单放过。
