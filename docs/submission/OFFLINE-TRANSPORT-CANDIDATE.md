# 离线源码／镜像运输候选（不是最终运行包）

2026-10-22，A负责。为第二机准备减少镜像仓库下载依赖，不要求购买云服务器。
当前仅运输已验证候选；最终版本冻结后必须重新打包，不拿旧 a35 镜像代替新功能。

## 输入及安全范围

`scripts/windows/export-portable-candidate.ps1` 接受归档构建报告和同版真实默认
CMD/HTTP 成功报告；要求 source、镜像 ID、源码 ZIP 哈希、源码审核结果一致，
且原专项 proof/cleanup 均为0。输入限本工作区私有 evidence 路径，拒绝链接路径。

工具仅从本机缓存保存应用、PostgreSQL18、固定 digest Mosquitto 三个 Linux/amd64
镜像。镜像缺失直接失败，不 pull、不构建、不改标签、不启动容器、不导出原业务库、
不读取真实运行配置、不推送 registry。不改宿主 Python，不清理旧成果或缓存。
先保守核对磁盘空间，输出为私有 work 下新随机目录，失败保留 incomplete 状态。

输出只含 source.zip、images.tar、manifest.json、export-status.json。
manifest 逐项记录来源完整 SHA、三个精确镜像 ID/platform、两个文件大小及 SHA256。
镜像归档以 ID 保存而非共享标签；仍须核对 archive 内容，不把标签当来源证据。
该步骤不是镜像历史/二进制完整安全审计，不可据此直接对外分发或参赛发送。

## 操作与验收

在项目目录运行现有 PowerShell，无需安装依赖：

```powershell
.\scripts\windows\test-portable-candidate-guards.ps1
.\scripts\windows\export-portable-candidate.ps1 -BuildReport <application-build.json> -SmokeReport <application-http.json>
```

护栏17项合成验证包含错 SHA/状态/镜像/源码审核、烟测与清理不一致、输入越界；
它们不是导入、应用启动或第二机验证。运行前应检查空间，不自动清理已有文件腾空间。

另一电脑接收前需核对 manifest 的文件大小/哈希、Linux amd64 Docker Desktop 可用，
确保无占用的项目名、端口和真实数据目录。**不要未经检查直接覆盖旧部署或导入数据库。**
当前没有完整 worker 启动与私有通知配置交付，故本工具不能作为一键完整应用启动器。

同一 Docker daemon 重新 load 归档，只能证明该归档可读且镜像身份仍可解析；
不能代替第二台电脑从包启动、正式迁移、全部 worker、页面业务、故障/重启及两通知。
最终 M02 必须补完这些出口，另记录第二机操作者、系统、版本和真实结果。

## 当前证据

实际以 a35a1e3f31db4dc89d5b1dc5b04f022761ef1e65 候选运行，来源为已核对的
归档构建和 image-http-20261021195608-9e9adb8ab601 专项。源码 ZIP 2,176,603 bytes，
SHA256 `93ad9fd00ecf3180f19ecf05b20572110537ad33a8dc4e7825db2f1dd08c4dca`。
首轮镜像归档722,549,248 bytes，实际读取 archive manifest 包含3镜像、RepoTags全部空，
两文件哈希独立回读一致。首轮后增加输出源码哈希再次比对、manifest写成功才置完成；
最终实跑记录另随本轮证据补充，不把首次输出当作最终工具的精确来源。

没有真实数据库迁移、部署、网络通知或第二机验收。M02仍未签收。

最终工具工作树实跑目录：
`work/portable-candidates/20261022003137-eb9921760db842cd9b327e24a1f75034`。
镜像 TAR SHA256 `a66c1d3ed423e6578f3add131310fa976c94613cd34eec276b2bd54f957495ac`。
实际同daemon `docker image load` 成功，三镜像ID逐一与manifest一致，加载前后
完整镜像ID集合不变。归档字节/哈希随每次Docker导出可能变化，不能拿首轮哈希
核对本轮文件。未pull/运行容器、未替换标签、未操作原数据库；第二机仍待本人验证。
