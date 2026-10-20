# M02 应用镜像候选构建：不是最终可运行包签收

2026-10-20，A负责。复用M01源码导出工具，不从工作目录直接构建，也不把旧缓存
`freshlink-app:p0`重新打标签冒充新应用。当前输入仍可能包含未合并B/C候选，不能据此
称正式迁移或完整E1通过；最终参赛版须重新按冻结版本构建并实际复核。

## 输入、操作与输出

输入为当前干净工作区的完整Git SHA。脚本先从该提交导出源码候选，实际解包并逐字节
对照Git树、检查私有路径/有限凭据模式；只将解包目录作为Docker上下文。不会挂载真实
`.env.freshlink`、`.git`、本机work或原数据库，不运行宿主Python/uv/pip。

构建使用已验证Bun1.3.12镜像digest、完整workspace manifest和`bun ci`锁校验，Python
3.14.8与既有`uv sync --frozen`；安装/编译发生在容器内，不修改宿主包和锁文件。
生成唯一`freshlink-application-candidate:<SHA前12位>-<随机ID>`镜像，不覆盖原应用镜像，
不运行部署、不上传registry、不保存大型镜像包、不删除旧镜像/缓存。

镜像标签只是来源声明，不能单独当证明；构建后再于断网容器中对照导出manifest，核对
镜像内后端app/scripts/pyproject/alembic文件集合与字节，并检查前端index/JS确实存在。
多余配置、改字节、缺文件、路径穿越与符号链接必须失败。该检查不证明接口、默认启动
命令、数据库迁移、MQTT worker、浏览器业务或完整镜像安全审计通过。

在项目目录使用现有PowerShell执行（完整SHA需先保存并审查）：

```powershell
.\scripts\windows\build-application-candidate.ps1 -Revision <完整40位SHA>
```

结果在本次唯一的`work/source-candidates/.../application-build.json`，源码ZIP、逐文件
manifest和解包目录同时保留。失败也保留来源与错误，不清理旧成果、不降低TLS或完整性
校验。源码标签、镜像ID、源码ZIP哈希和检查结论分别记录，不能只写“build成功”。

## 后续M02仍需验收

候选构建/源文件核对通过后，还要验证镜像实际默认启动、空库/已有数据升级、正式唯一
迁移链与A/B/C持续worker接线；再从交付物在第二电脑启动，验证完整业务、权限/失败/
重启和两通知。可运行包应包含所需镜像/启动与无秘密配置说明，但不分发应用数据库。
在这些步骤和同版M01/M03/M04/M05未完成前，M02仍标“未签收”，不部署或对外投稿。
