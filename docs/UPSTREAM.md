# 上游来源

- 仓库：https://github.com/fastapi/full-stack-fastapi-template
- 取得日期：2026-10-09
- 固定提交：`7257e606cb94eada1dca40173e2b7f99dd549bc6`
- 许可证：MIT，原 `LICENSE` 保留。
- Git 远程名：`upstream`。不是团队自己的仓库，不向此远程推送。
- 上游代码含基础认证、数据库与示例页面；团队的冷库业务尚未实现。
- 上游后端要求 Python >=3.14，前端包管理使用 Bun。本地 Python 3.12 不能直接运行该版本后端，P0 采用容器环境隔离。
- `bun.lock` 和 `uv.lock` 保留；不得无说明升级依赖。基础镜像标签沿用上游或本项目配置，成功构建时记录实际镜像 digest，当前不声称已实现跨日期 bit-for-bit 复现。

新增：`AGENTS.md`、`START_HERE.md`、本项目 docs/contracts、Windows 脚本和 `compose.freshlink.yml`。使用独立私有 `.env.freshlink`，不往上游跟踪的 `.env` 写入真实账号。

当前未执行上游 Python/前端测试或容器构建。2026-10-10 用户截图显示 Docker 桌面端引擎已运行；Agent 受限环境无法连接本机 Docker 管道，待用户终端执行首次启动，状态记录在交接文件中。
