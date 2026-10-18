# 可选共同基线真实启动检查（待审，不新增 S0 前置）

供没有本机 Docker／Python3.14 的成员使用。先有同头独立审查及适用CI，再正常
合入main；**本文件刚上传时不代表 Actions 的手动按钮已经可用**。有本人可用
本机或云环境的成员可直接真实检查，不必等这个辅助入口，更不增加一轮接口签收。

合入后，B/C各自使用本人账号／授权Agent，在新库 Actions 选择
**Shared baseline real HTTP → Run workflow → main**。Agent有实际Actions写权限
可正常dispatch；权限不足才由本人点一次，不共享A的凭据。不是定时监控或部署。

此工作流仅标准 `ubuntu-24.04`、只读 contents 权限、公有团队库运行，最长12分钟；
私有库条件直接不执行，不调预算、不用大型runner、不上传报告附件、不发邮件/APP。
标准公有runner费用边界见[GitHub官方说明](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)；
服务网络/可用性不能由本项目保证，运行失败须分辨依赖下载与实际测试失败，不反复空跑。

它构建临时测试镜像和内网tmpfs PG；在任何迁移前先做Docker/来源/临时库拒绝守卫，
预检不连接数据库、不启动进程。通过后升级**该临时库**到本次源码的唯一head，启动
真实Uvicorn子进程，检查health、匿名401、密码登录、活跃管理员回读、注册初始unknown、
重复409且数量仍1、列表及详情。无宿主端口，不使用原应用服务/卷/凭据，结束只清本次
项目。Python只在容器运行；禁止拿此说明去安装/同步/卸载宿主Python环境。

PR检查只标candidate。手动main检查须同时匹配干净HEAD、origin/main及main ref，
结果记录完整source SHA、实际actor、scope、版本和真实迁移head。记录不会含密码、
JWT或DSN；原日志仅留临时结果，不自动上传。不能把A触发的run说成B/C本人亲测。

本人读取实际成功run及其JSON后，在#5给：角色、run链接、完整source SHA、
本人分支完整HEAD、是否包含该main祖先、真实结果和未覆盖项。**先保护未提交成果，
普通merge已发布main；fetch成功不等于本分支已包含main。**

这只提供共同基线的云端真实API运行证据，不是本人Windows运行、浏览器交互、
B/C正式业务接线、两通知实收、完整E1或第二台电脑从最终包复现。S0仍按既有包
及#5真实条件收口，最终10月30日及之前实际投递。

## 2026-10-19 审查返修：优化模式不漏检

C 对54c6ef6的review5563887128指出：`assert`会被python -O/PYTHONOPTIMIZE移除，
不能用作验收PASS门禁。当前已将全部实际HTTP/权限/计数/详情条件改为显式失败，
不以默认环境曾经通过而忽略此风险。

新增8种子进程 `python -O` 故障注入：匿名、登录、用户回读、空列表、注册、重复、
数量及详情响应依次故障，每种都须非零退出、不生成成功报告，并执行服务端清理。
这些是明确的Mock负向回归，不能说成真实HTTP成功；真实进程/API成功仍另跑。
本轮完整隔离后端313项/95%通过（原305项＋8），未降低覆盖率或跳过门禁。
新提交同头CI/真实HTTP和C独立复审仍按实际记录，未批准不得合入或宣称手动入口已发布。
