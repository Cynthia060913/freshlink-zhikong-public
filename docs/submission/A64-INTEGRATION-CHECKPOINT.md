# A64：2026-10-26 晚集成与交付核对

本页记录 A 候选的实际补证，不是 main 发布、完整 E1 或最终参赛签收。
共同 main 当次核对为 `426604f2933a3b2d594f8fad6019dfdea84e75cf`；实际最新状态仍须回查。

## 已接入的作者成果

- C `5b8018e19fda01f44f1c6f51ad83d7a95b33b045`：正常合入原文严格复核和合法
  revision-only 续验，保留原作者历史，不再以私有 overlay 充当 C 正式实现。
- B `d1f5359959423fa463cfdee5500c6b2486b39e9f`：关联更正、鉴权回归、受控 PG
  门禁及本轮 world 清理；已包含 C PR83，更正 payload 自身指标与原异常均受限。
  集成时发生一次重复关键字参数，被导入检查拦截；A 去除重复项后运行下述实际回归。
- Claude PR84 `4237e56dfbaa7cfce2e95831823d0d0ccfb5fe7a`：四提交八文档，
  相对任务书起点只改八文件，TASK.md 未变。正常合入 A 候选，不直接合 main。
  原底稿仍标 bfb 来源和未验收，不冒充当前冻结版。
  云端 typos 将紧连中文的 SHA 片段误识别为英文词；A 先修允许修改的交接文档排版。
  TASK.md 曾因原只读边界被自动审批拦截；队长在本轮确认采用最佳建议后，A 仅为其中四个
  SHA 加空格与反引号，未改提交值、任务要求或业务代码，也未跳过/关闭拼写门禁。

## 当前组合的实际运行

后端组合 `f3020df853575361f4cc31d9313d64603337ca81`，Python 3.14.8/PG18，
缓存镜像提供依赖、当前 backend 只读挂载；这不代表 bfb 镜像内已更新为 f302。

1. 限定组合 120 passed / 0 skipped，含准备调度十四用例、多批窗口、坏原文、严格
   PG、高温/更正、B 更正鉴权。原始报告
   `work/notify-subset-78bd19df331d4499bf4b2f4b492efd67/canonical-c5b-bd1f.xml`，
   SHA256 `75cd7951132fcc6e2d31468bac6d934bf40bbe60f2a9686030ea0f0e717afa2b`。
2. 完整后端 1433 个 testcase、0 failure/error/skipped，覆盖率 91%。原报告
   `work/a01-tests-20261026205716-65640/pytest.xml`，
   SHA256 `193d9c1b92da632cc362d4327479f032bdc72190049ec7a8e60746878716b2ca`。
   XML 汇总 tests=1742 与 testcase=1433 的差异不能当作新增主用例；同版本 pytest
   的纯合成 subTest 探针已复现此统计口径。旧 69/72 差异见 Claude 底稿 02。
3. 真实 PG/C01、内部 MQTT、四独立遥测进程、后台关联更正、TCP B 回读、设备执行 ACK
   及后续数据通过。报告
   `work/gated-df9af2d72d2b433c9bf1c12f670e17f4/runtime-composition.json`，
   SHA256 `b8fa249fb825a1c3ed028a9fc4ce1f925e5c4cb12c4ae8bb69ad6eaa34a645f0`。
   恢复 HTTP 仍由显式验证调用，notifications_sent=0，complete_e1=false。
4. 十四设备组合 `5bdac7e05d37e06af0b0aabff25d03683376db59` 的 backend/frontend
   与 f302 相同；两库三苹果批次、七实际 MQTT 传感流、七执行 ACK/后续降温、十四
   离线/重连追溯、重启保留与同筛选 CSV 通过。报告
   `work/gated-fleet-608af008bdb1436296b62e8f522ca643/gated-fleet.json`。
   正常评价已覆盖 WH1 两批；此轮尚未评价多批高温，后续增量须另记版本和结果。
5. 随后的 A 验证脚本增量在干净组合
   `319d72211b980866af26b34765fb2ea81036bb09` 实跑通过：保留正常规则下的旧结果，
   经 B API 追加并 supersede 为测试专用阈值 1°C 的历史新规则，取两库新到的真实 MQTT
   样本分别生成 WH1 两批、WH2 一批三个高温候选。三个独立来源经鉴权 ASGI HTTP
   实际写入 B，按来源回读批次/规则版本匹配，重建 sender 后零重复投递。
   这是受控规则切换的工程验证，不是果品温度真的升高，也不是生产温控阈值。
   此段 B 传输为 TestClient/ASGI，不能冒充 TCP；真实 TCP 证据在第 3 项单独记录。
   报告 `work/gated-fleet-b9a7097958af4e8d8d756f1563c977d9/gated-fleet.json`，
   SHA256 `a763a89be36f1e8c48e7ad09c2455d66012a1f567f576c99af6f43071cf49107`。
   同时重验了十四设备、控制/离线恢复、重启与 CSV；仍无 C 恢复/通知/第二机签收。

上述本轮临时容器/网络及业务子进程已清理，原数据库/宿主 Python 未修改。
证据保留于 A 本机 work，公开仓库不包含这些私有运行目录；最终交付须汇编经过检查的证据副本。

## 应用归档交付工具

新增 `scripts/windows/export-application-image.ps1 -BuildReport <已成功的构建报告>`。
只接受本工作区 source-candidates 的成功来源审计报告，按不可变镜像 ID 导出，检查
Docker/OCI 索引到 config 的关联及源码标签、输出字节数/SHA256，拒绝带标签归档。
不导入、不 retag、不 push、不启动，不自动删除失败产物。

工具已对原 bfb 镜像实际导出 540471808 字节，归档
SHA256 `b945711b0ec74776df5f0ee87c4e7a9f9474c4890ff9b0361e995aadf6791a5e`；
记录 `work/image-archive-20261026211159-59306eb0d336/image-archive.json`。
外部报告路径/未验证报告两负例均在 Docker 与输出创建前拒绝。最初按传统 config ID
比较的尝试拒绝现代 OCI index ID，失败产物保留；按实际索引结构校验后成功。
这不是完整镜像安全审计或第二机导入。正式冻结后须重建/重导出最终同版应用；运行依赖
PG/MQTT 和启动配置仍需随交付说明准备，不复制个人数据库或密钥。

## 未关闭的必选出口与责任

- A+C：受保护的恢复 freshness/claim/提交后发送、无人值守重启/超时与迟到更正。
  不把只读 preflight 与另一个事务的 generic claim 拼成原子协议。
- B：复用 A `app.modules.iot.notification_service` 的私密收件范围/默认 dry-run/
  显式 send 封装，核对实际适配器；账号持有人本机配置、具体发送许可与两终端实收。
  B 裸 runner 的默认空 recipients 不是正式接线入口，不重做已有封装。
- C 协调机器持有人：真正另一台电脑从同版交付包复现。无 Docker 时先报告并由本人决定
  环境准备；同机换容器不算第二机。
- A/B/C：当前精确头独立审查、适用检查及具体 main 合并许可；随后统一版本 E1、
  M01—M05 成品核验和真实投递凭证。CI/分段/归档文件均不能替代这些出口。

27 日冻结节点仍有风险；只收敛必选主链，不加算法或装饰、不靠改日期抹去 20 日延期。
