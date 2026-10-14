# 鲜链智控：新仓库迁移与当前事实入口

STATE: WAIT_VERIFICATION

本页优先于下层历史说明。用户已批准按检查点迁移；代码和任务已迁入，
远端及全新克隆核对通过。**公开后的基础CI已实跑通过；尚待B/C本人接入、
本机未上传增量清点及交叉审查，不宣布三人正式切换。**

2026-10-14补查：A旧本机14条分支和5个工作区已核对，未上传旧库的心跳持久化、
MQTT服务端接收、控制事务/API及A/B接线提案均有新库对应版本。
具体版本、实际测试与边界见 [本机补查与继续开发条件](MIGRATION-FOLLOWUP.md)。
B/C必须各自执行 [未上传成果迁入与持续协作](UNPUBLISHED-WORK-TRANSFER.md)，
不能由A代替确认两台电脑是否存在遗漏。

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

完整导入时的SHA、文件数量和变更边界见 [PUBLIC-MIGRATION-INVENTORY.json](PUBLIC-MIGRATION-INVENTORY.json)。
所有PR保持草稿，原审查/CI不冒充新仓库结果。旧文档的URL、PR编号与SHA仅为私有历史。
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
6. 负责人完成检查后将本页改为ACTIVE，三方才正式切换。后续每次恢复任务自行读最新评论和依赖，
   每个可保存交付更新本人Issue，不再要求A逐条转述；GitHub不会自动唤醒闲置Agent。

默认CI不部署、不外传覆盖率、不自动上传报告附件、不提高预算；只有标准ubuntu-latest。
发布部署、付款、真实邮件/APP发送、破坏数据仍须单独授权；本次不合并未经审查的A/B/C。
