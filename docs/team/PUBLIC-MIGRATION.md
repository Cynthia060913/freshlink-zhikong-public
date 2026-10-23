# 鲜链智控：新仓库迁移与当前事实入口

STATE: ACTIVE

2026-10-22 本轮最新：27日前必选完整实链/第二机并冻结，28—29同版材料复核
尽早投递，最晚30日前实际发送，不默认30日深夜；下层27日实际投递为旧要求。
S0最小已完成，原20日E1延期保留，ACTIVE不代表业务/提交全部通过。

2026-10-21 最新截止：**10月27日及之前实际投递，25—26日复核成品**。
下方30日是旧计划，不再使用28—30日缓冲。当前事实/候选版本每次刷新GitHub，
按[最终一周计划](FINAL-WEEK-PLAN.md)与[交付检查点](DELIVERY-CHECKPOINTS.md)
推进；ACTIVE仅为协作迁移，不等于正式业务链和参赛投递已完成。

## 2026-10-19 23时实际核验（优先于下面历史）

三方本人已接入新库，固定 f98 契约同版接受、已发布 main e235 的取得和三位
本人身份触发的真实 HTTP 运行均已核验，见 [S0 真实收尾记录](S0-VERIFIED-20261019.md)。
全队开发入口正式为本新库；旧库继续保留 Private，不把旧 Git 历史推入新库。
ACTIVE 仅表示协作迁移与 S0 最小接入就绪，不表示正式 B/C 业务、完整 E1、
第二机或参赛材料通过。下层 WAIT/未签收与旧 SHA 是带日期历史，不重新冻结开发。

本人2026-10-15最新要求：**10月30日及之前实际提交**，见
[共同日期/S0/E1检查点](DELIVERY-CHECKPOINTS.md)，优先于下层历史计划。
PR16的协作文档已审核合入共同main fb6de8408c860b277471cdf1697b72fd34162f38；
三方读/评论/本人普通push均有实际记录，C Connector写入也已由本人回读确认。
S0同版契约签收/当前基线取得和业务接线仍有缺口，不能以迁移或CI成功代替。
下面标日期的版本/等待描述为当时快照，每次继续以实际远端为准。

本页优先于下层历史说明。用户已批准按检查点迁移；代码和任务已迁入，
远端及全新克隆核对通过。**公开后的基础CI已实跑通过；B/C均已本人报告接入。
尚待当前main安全同步/签收和迁移文档审查收尾，不宣布全部检查点完成。**

2026-10-15：共同main为 `f7fe62a95eceee75bebba3e135fc67d6c7728643`，PR15已由
B交叉批准、A网页合并，新main适用检查通过。C已在#1的6181504937确认新库读取、本人
评论、普通推送和已定位成果补查；当前PR11 HEAD为`0a808f71a9bf5cd0416073e7601817864948d1a8`。
C其他未定位目录未完整检查，保留该边界，不把有限清点说成整机无遗漏。

2026-10-14补查：A旧本机14条分支和5个工作区已核对，未上传旧库的心跳持久化、
MQTT服务端接收、控制事务/API及A/B接线提案均有新库对应版本。
具体版本、实际测试与边界见 [本机补查与继续开发条件](MIGRATION-FOLLOWUP.md)。
B/C必须各自执行 [未上传成果迁入与持续协作](UNPUBLISHED-WORK-TRANSFER.md)，
不能由A代替确认两台电脑是否存在遗漏。

成员接入和全队切换分开记录。B已提交本人接入/无漏迁确认，并批准PR15；本轮后续已回读
B远端`61281c05936aa4f42c3c0b42ee079c718df7fb12`（包含f3f570b），新库增量已实际取得。
A独立220项通过，但两个附加故障边界仍需修复，见PR10复审。B分支尚需正常同步当前main，
不强推。C本人接入/推送已实测，不再等待邀请或本机修复上传的旧前置。
已完成本人接入的成员可将补漏与本角色修复推到新库本人功能分支、更新PR和
评论，不必等全队ACTIVE；依赖公共契约/正式迁移/接线的操作仍须按#5确认。
WAIT_VERIFICATION只表示全队签收未齐，不冻结已就绪成员的安全功能分支开发。

## 仓库与版本

- 新开发入口：https://github.com/Cynthia060913/freshlink-zhikong-public
- 原 freshlink-zhikong 保持 Private，原分支/Issues/PR/历史不删除、不强推；
  原目录和未提交改动保留。本机完整历史备份只留私下，不上传新库或参赛包。
- 新稳定源码基线 `a548ede7f9eed7353b687e63e263c678608488e7`，来源旧main
  `0bf33f86c1fde9af1cb7e7e5a9a74d411b42f813` 加CI/公开检查安全门。
  这是不可变的迁移代码基线；后续会有本页/CI安全更新，**每次开工另读远程main完整SHA**。
- 五条新历史分支：main、feature/b-notification-bootstrap、
  feature/c-data-quality-bootstrap、feature/a-control-storage、feature/a-b-integration-review。
  A/B/C待审不等于稳定main已含全部业务。

## 当前协作任务（均在新仓库）

|用途|入口|
|---|---|
|迁移检查点、成员确认|[#1](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/1)|
|公开与权限安全|[#2](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/2)|
|源码完整性与版本映射|[#3](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/3)|
|私有历史说明|[#4](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/4)，不是旧PR4|
|S0共同契约及接入确认|[#5](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/5)|
|E1小场景真实闭环|[#6](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/6)|
|A设备通信控制运行|[#7](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/7)，@Cynthia060913|
|B仓储告警通知材料|[#8](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/8)，@xixidamei|
|C数据质量历史导出|[#9](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/9)，@im1yt|
|B待修审查|[PR10](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/10)，源58546e9|
|C真实入库待做|[PR11](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/11)，源6f38c1c|
|参赛功能/材料签收|[#12](https://github.com/Cynthia060913/freshlink-zhikong-public/issues/12)|
|A心跳MQTT/控制API待审|[PR13](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/13)，源dec9660|
|A/B权限接线和RC2提案|[PR14](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/14)，源eb131c3，依赖B|
|A控制真实MQTT往返候选|[PR18](https://github.com/Cynthia060913/freshlink-zhikong-public/pull/18)，依赖PR13|

完整导入时的SHA、文件数量和变更边界见 [PUBLIC-MIGRATION-INVENTORY.json](PUBLIC-MIGRATION-INVENTORY.json)。
业务候选仍待审，PR15的CI基础已合并；原审查/CI不冒充新仓库结果。
旧文档的URL、PR编号与SHA仅为私有历史。当前HEAD和检查必须实时回读。
接口以三方对同一契约提交明确签收为准；S0和E1仍未完成。

## 已核对与保留边界

- 原main/B/C最新远端SHA分别为0bf33f8/58546e9/6f38c1c，导入前已通过API核对。
- 五快照无原commit/PR refs/tags，作者元数据为维护者noreply；原贡献归属见CONTRIBUTORS.md。
- 逐Git blob、文件模式和远端树哈希核对；代码、锁、迁移、协议样例、v4、参赛材料清单保留。
- 只省去四个继承的编辑器skill符号链接和B十一个硬编码旧任务的一次性post脚本，原私有库仍在。
- 旧令牌/收件地址/个人作者邮箱历史、旧Actions/评论全文、work/数据库/真实配置不搬到公开库。
- 有限模式扫描和图片来源核对不等于完整安全审计；发现新秘密先撤销并控制暴露，不通过改回Private假称已收回副本。

## 正式切换前每位Agent只需做的一次接入核验

1. 本人接受新库写权限邀请。公开只代表可读，不代表本人Connector/发帖身份已授权。
2. 保护旧目录未提交文件和真实配置；新建目录克隆新库。禁止旧history的pull/merge、mirror、强推或上传bundle。
3. 使用本人GCM/授权Connector及noreply；不共享A令牌，不把Token写进源码/聊天/日志。
4. 读取本页、AGENTS/START_HERE、v4/08持续任务、v4/09完整目标矩阵、本人Issue、#5/#6/#12及相关PR最新SHA/检查。
5. 在#1报告本人账号、main/分支完整SHA、认证可读/发帖/推送的实际验证（未测写未测）、旧成果是否保留。
   真实启动/登录/设备页及接口确认在#5记录，不代他人签收。
6. 本人接入确认后可在新库本人功能分支保存补漏、修复和独立开发，并回读远端SHA/CI；
   不因其他成员未接入而冻结本角色。负责人收齐三方检查后将本页改为ACTIVE，标记全队切换。
   后续每次恢复任务自行读最新评论和依赖，
   每个可保存交付更新本人Issue，不再要求A逐条转述；GitHub不会自动唤醒闲置Agent。

默认CI不部署、不外传覆盖率、不自动上传报告附件、不提高预算；只有标准ubuntu-latest。
发布部署、付款、真实邮件/APP发送、破坏数据仍须单独授权；本次不合并未经审查的A/B/C。
