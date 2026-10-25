# A47：实际架构、ER 与数据字典（M03 候选）

2026-10-24。业务源码固定 `71e5a8820ee966db1b250afaf0a72143a4bdc62b`（PR59，依赖PR58），
共同main仍 `a1d185eea504f4d389a65c9431bf0e62d4827cf6`。以下是已核对的候选材料，
不代表候选已合main、完整E1、M03全文签收或实际参赛投递。

## 可直接用于正文的图件

|图|内容|可编辑来源|矢量成品与预览|
|---|---|---|---|
|系统架构|当前展示/API/设备接纳/数据/仓储/消息与存储层；通知实收仍虚线待验收|[TeX](tikz_architecture.tex)|[PDF](tikz_architecture.pdf) / [PNG](tikz_architecture.png)|
|A核心ER|设备、心跳、在线离线事件、控制命令/尝试/执行回执|[TeX](tikz_er_a.tex)|[PDF](tikz_er_a.pdf) / [PNG](tikz_er_a.png)|
|B核心ER|冷库、批次、规则、事件、处置变化、渠道尝试|[TeX](tikz_er_b.tex)|[PDF](tikz_er_b.pdf) / [PNG](tikz_er_b.png)|
|C核心ER|观测、接纳审计、质量、评价、持久候选、快照及成员|[TeX](tikz_er_c.tex)|[PDF](tikz_er_c.pdf) / [PNG](tikz_er_c.png)|

图题使用“系统架构图”“设备控制ER图”“仓储告警ER图”“观测快照ER图”，不要把
整段解释焊进图内。PDF为单页矢量，PNG供PPT与预览；正文插入按实际尺寸检查可读性。
ER箭头从被引用父表指向引用子表，只表示真实FK，不表示父表必须有子行。复合PK逐行
列出。C图为可读性使用 `C:` 简称，精确表名/字段映射见数据字典和 `er-edges.json`。

## 真实证据来源

复用已从精确71e5 Git归档构建并逐文件核对的应用镜像。只在随机专属内部网络/
tmpfs PostgreSQL18空库执行 Alembic A03→B01→A04→A05→C01 和 model check；
随后只读事务反射33张表的字段、PK、FK、唯一约束及索引。不连接原库、不读取业务行、
不保存数据库URL/密码/收件地址。`schema-inventory.json`保留反射结果，原始未补终止换行的
反射输出SHA256如下；仓库规范只补终止LF，检查仅忽略该终止LF，不忽略任何字段变化：
`d43cc6519a7ca324e2a3dcff30999e7d258222e5de6cec72ca7a6d8f7c432bd5`。

[完整数据字典](DATA-DICTIONARY.md)覆盖33张业务/模板表；三张ER图只展示19个核心实体、
17条已逐项与实际数据库FK核对的边。没有绘出的收件队列、前沿、冲突审计及恢复进度等
不等于不存在。`iot_device.warehouse_id`、C观测的设备/冷库UUID、outbox与B来源等有些
是业务逻辑引用，并非DB外键；不能按字段同名擅自补线。原文/归属/epoch/冻结戳的一致性
仍靠实际调用方事务门禁、权限和回读，不能靠一张ER图宣称完成。

## 已检查与保留的限制

- 四图XeLaTeX实际编译、Poppler实际PNG渲染并逐张人工视觉检查；无文字截断/节点重叠。
  技能结构检查全部0 CRITICAL，无Overfull和缺字；保守宽度估计/无粗箭头提示保留，
  未因纯风格提醒重画。工程框图统一主色及浅色，黑字、白底、虚线标注待验收。
- 本机缺Draw.io，采用确定性可编辑TikZ兜底；已有xeCJK依赖缺失，禁止自动安装后，
  用已存在Microsoft YaHei字体和手动换行成功，没有修动系统Python/LaTeX配置。
- 首次反射器直接用原始URL默认选择未安装psycopg2，被拒绝；改用项目既有规范化
  settings URL/psycopg后通过，没有安装额外数据库驱动。临时资源每轮均清理。
- 图中的组件存在不表示所有后台调度已启用；多批次评价、C异常恢复/发前复核、
  B独立持久派发、SMTP＋飞书两端实收、完整E1、第二机仍未签收。
- 不代写B/C本人贡献或虚构实验；算法未启用，不把阈值异常称真实腐败概率。

复核材料完整性及真实FK关系：

```powershell
python scripts/check-design-evidence.py
```

此脚本只读本候选材料；通过只表示图/证据一致，不替代当前数据库或业务验收。
最终冻结版有源码或模式变更时重新反射/生成/实际看图，不能只改图的SHA标签。
