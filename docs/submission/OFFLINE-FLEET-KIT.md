# 第二机离线分段验证包（候选，非完整 E1 或最终应用）

包包含固定候选源码 ZIP、逐文件清单、应用/PG/MQTT 三个 Linux amd64 镜像的无标签
归档、合成 MQTT ACL、可审查 PowerShell 入口。不含真实配置、数据库、个人收件地址
或宿主 Python。接收端仅需已有并启动的 Docker Desktop（Linux amd64）及 Windows
PowerShell 5.1 或 PowerShell 7；无需 Git、Python、Bun、联网安装或预存镜像。
不自动安装/修复 Docker，不修改网络、系统或 Python 配置，不支持远程 Docker。

## 核验与运行

1. 由 A 通过队内认可的方式提供整个目录；另行取得 `offline-manifest.json` 的 SHA256。
   **不要只信同一个包里的一份哈希**。哈希用于校验传输一致性，不是软件签名或全面安全审计。
2. 将完整目录放在短路径（例如 `D:\FreshLink-kit`），先人工查看入口与说明。保持包目录
   不变，不放额外真实配置；结果自动放在包的同级随机 `offline-fleet-*` 目录。
3. 在 PowerShell 先进入实际包目录，再执行以下命令，把占位文本替换为另行收到的64位哈希。
   不设置宽泛 ExecutionPolicy；若组织策略阻止脚本，请停止并反馈，不绕过。

```powershell
Set-Location -LiteralPath 'D:\FreshLink-kit'
.\run-offline-fleet.ps1 -ExpectedManifestSha256 '另行取得的64位小写SHA256'
.\run-offline-fleet.ps1 -ExpectedManifestSha256 '另行取得的64位小写SHA256' -RunFleet
```

第一个命令只读核对所有文件、镜像归档和源码 ZIP 每个文件的字节，不调用 Docker。
第二个先重复核验，再本地载入三个内容寻址镜像（不覆盖已有标签），在独立随机内部
网络中创建 tmpfs 临时 PG 和 MQTT；无宿主端口、真实通知或后端源码挂载。正常与失败
都会尝试只清理本次创建的容器/网络，保留报告和镜像，不清其他项目/卷/缓存。
若窗口强制关闭或 Docker 中断，查看 `offline-cleanup.json`；清理未确认不能报完成。

## 实际证明范围与回传

运行的是现有固定来源的 `check-gated-fleet.py`：实际 C01 Alembic/model check、两库
三批/14逻辑设备采集、7执行 ACK 与后续降温数据、离线/恢复在线追溯、同状态重启及
同快照 CSV。首次注册 ID 是合成 ACL 夹具；离线阈值3秒仅测试，不改生产默认90秒。
当前多批评价仍会 hold；不证明 C 自动异常恢复、两通知实收、完整 E1 或最终 UI 成品。
应用镜像来源和验证工具来源分别记录，材料改动不伪装成重建了业务镜像。

接收者在 #6 / 本人任务回传：本人在**另一台实际电脑**运行、应用完整 SHA、工具 SHA、
另行核验的 manifest 哈希、`gated-fleet.json` 与 `offline-cleanup.json` 哈希和结果、
实际失败与未测项。无需发送机器用户名、完整私人路径、登录密码或 Token。
A 同机新目录测试、CI 和容器更换不能代替接收者本人第二机确认。

## A 构建入口

在干净、已提交的工具分支上运行 `scripts/windows/export-offline-fleet.ps1 -BuildReport`
指向已有 `application-build.json`（同目录应有已验证源 ZIP 与 `manifest.json`）。
仅使用缓存镜像，输出到私有 `work/offline-kits/随机目录`；缺依赖立即失败，不自动拉取。
传包和正式发布分别遵守团队授权；不要把大镜像归档提交进 Git。最终源码/镜像/材料
同版冻结、全部必选、真实双实收和投递凭证另行签收，本包不提前勾选 M01—M05。
