# Windows 接入共同仓库与小交付

## 1. 三个人分别操作什么

B/C 各自注册 GitHub 并验证邮箱，只把用户名发给 A，不分享密码/验证码/Token。无需另建仓库、Fork、购买服务器或 GitHub 套餐。Git 是本机版本工具，GitHub 是共同远程仓库。

A 在共同仓库页面→Settings→Collaborators（可能显示于 Access 区）→Add people，搜索各自用户名，确认正确用户后发邀请。B/C 在自己的账号接受邀请，再打开仓库确认看到文件。私有仓库 404 先查登录账号和邀请权限，不改成公开。

账号操作由本人完成；不向 Agent 提供 GitHub 密码/验证码。安装 Git for Windows（官方来源），保留凭据管理器组件；通常 HTTPS 克隆会打开浏览器由本人认证，不把访问令牌写进 clone URL。Docker Desktop 用 Linux 容器；需要管理员或重启先保存，不能擅自改系统策略。

## 2. 克隆：已有代码先保护

如果已有旧文件夹，不覆盖、不删除、不在其中重复初始化。让 Agent 先清点现有代码/未提交文件/已验证功能，再把共同仓库克隆到新的、不存在的短路径。以下以 `D:\freshlink` 为例：该目录已存在就停下换新路径，例如 `D:\freshlink-team`。

```powershell
git --version
git -c http.sslBackend=schannel clone https://github.com/Cynthia060913/freshlink-zhikong.git D:\freshlink
Set-Location -LiteralPath D:\freshlink
git remote -v
git status --short --branch
git log -1 --oneline
```

必须确认 origin 是 Cynthia060913/freshlink-zhikong，不推上游 FastAPI 模板。若缺盘符/写权限选择自己可写目录；若网络失败保留原错误给 Agent，不关闭 TLS 校验或防火墙。已成功克隆后不要再次 clone 到同一目录。

A01 和 v4 当前尚未上传；首次克隆旧 main 正常。先做环境检查。收到 A 的新基础提交号后，Agent 在确认本地干净的前提下更新：

```powershell
git switch main
git -c http.sslBackend=schannel pull --ff-only origin main
git log -1 --oneline
```

有本地改动就先保护，不用 reset/checkout --/clean，不盲目 stash。把实际提交号回传给 A，确认新文件存在再开任务分支。

B 首轮：`git switch -c feature/b-notification-bootstrap`。
C 首轮：`git switch -c feature/c-telemetry-bootstrap`。
分支已存在先检查并切换，不强行重建。

## 3. 让自己的 Agent 打开仓库

用正在使用的代码 Agent 打开克隆出的项目文件夹，不是浏览器仓库页，也不是原有不明副本；确认其工作目录是新文件夹。把对应 `02-B-Agent指令.md` 或 `03-C-Agent指令.md` 的首轮文本发给它。

Agent 先读取 AGENTS、START_HERE、PROJECT、v4 和契约，再只读检查：

```powershell
powershell -NoProfile -File .\scripts\windows\check-environment.ps1
```

环境检查通过不等于项目能运行。Docker 就绪后由 Agent 在工具权限内执行，或请本人集中执行：

```powershell
powershell -NoProfile -File .\scripts\windows\start-and-verify.ps1
```

这个入口会下载/构建、启动容器并检查基础登录；当前不是全业务验收。首次需要联网和较长等待。遇到错误保留 work 的日志，不能清空数据库修复。打开 `http://localhost:18000` 查看页面；账号 admin@example.com，密码只在本机 `.env.freshlink` 查看，各电脑独立，不共享。

现有启动脚本不为某人自动授予 Docker 管道权限。Agent 无权访问时仅提供经检查的短操作，不暴露 Docker TCP 或改变安全策略绕过。

## 4. 保存、提交和 PR

Agent 核对 git diff、实际测试、文件所有权和私密文件后，用明确文件清单 stage，不盲目 add 全部。提交身份若未设置，询问本人选择的姓名及 GitHub 提交邮箱（可用 GitHub 提供的 noreply），不要编造或提交真实密码。

提交后推自己的任务分支，HTTPS 如遇同类 TLS EOF 可临时使用 schannel，不改变全局校验设置。示例只在正确分支且已有检查后的提交时使用：

```powershell
git -c http.sslBackend=schannel push -u origin feature/b-notification-bootstrap
```

C 用自己的 feature/c-telemetry-bootstrap。浏览器仓库页面选择该分支，点击 Compare & pull request；base main，compare 自己的分支。写清交付五项，不自行 Merge。PR 创建是外部写入，由本人操作或明确授权 Agent；Agent 成功创建后按工具要求附加 PR。不创建 PR 也可先回传分支和提交号供检查。

main 合并后，下一任务从更新后的 main 开新分支。旧分支有待交付内容时先报告，不能直接覆盖。公共迁移要按照 A01→B→C 的已合并顺序更新，模块作者负责修自己的冲突，集成者负责协调共享接线。

## 5. 固定回传表

优先写本角色的共同GitHub Issue/PR，不要求A人工转发；首次基线/启动/契约确认写S0评论，早期联调写E1。创建实际任务的方法和正文见 `05-新手操作与两个检查点.md` 及 `issues/`。工具未获访问授权时由本机本人操作或临时回传，不能假称已更新远程。

```text
角色：B / C
仓库目录：
基础提交号：
任务分支 / PR / 提交号：
完成项：
实际测试（输入、预期、实际、证据）：
未完成项 / 阻塞 / 需要本人操作：
共享文件或契约变化：
下一小任务：
```

未运行的测试写“未运行＋原因”。截图可脱敏发群；work 中原始日志只本机保存，先检查秘密再分享。不要分发 `.env.freshlink`、数据库、.git、node_modules、访问 Token 或授权码。

## 官方参考

- 邀请：[GitHub 邀请协作者](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)
- 克隆：[GitHub 克隆仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
- Windows 认证：[GitHub 凭据管理](https://docs.github.com/en/get-started/git-basics/caching-your-github-credentials-in-git?platform=windows)
