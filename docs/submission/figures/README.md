# M03／PPT 候选技术图

2026-10-22，源代码基准 `be88dea08e843f3d8e52f9d5907cf1d4fcd3ec87`。
这是章节配图，不是最终 M03 或全库 ER 签收。B 可用 PNG 汇编 PPT／Word，PDF
用于矢量插图，TikZ 源文件可编辑；最终业务冻结后必须核对并修订到同一发布版本。

## 图与含义

1. `tikz_system_architecture`：主要模块和消息/查询通路，不穷举全部 CRUD。
   双向设备箭头包含服务器命令与设备 ACK；raw 仍留存，清洗不是删掉真实超温。
   虚线模块/边表示仍待正式接线、审核或终端实收的部分，不是已经完成的闭环。
   C 观测源码/隔离验证存在不等于正式迁移发布；SMTP/飞书服务接受不等于终端实收。
   图中 B 仓储告警是已有业务核心，不表示最新通知增量已审核合入。
2. `tikz_core_er`：A/B 十个核心实体，父→子为实际外键方向；`1:N` 表示一个
   父实体对应多条子记录，`0..1:N` 表示子记录可没有对应冷库/批次，
   命令→回执的 `1:0..1` 对应每命令最多一条 receipt 的主键约束。
   表名跨行时按原顺序拼接。不是完整数据字典：心跳表、A队列、前沿、用户和 C
   观测/快照等未画进此核心关系图；最终完整 ER 尚须补齐已审定 C 迁移。
   不把 source_event_id 等业务关联冒充数据库 FK。

事实位置见 `../A-SYSTEM-DESIGN.md`、`backend/app/modules/iot/{models,control_models}.py`
和 `backend/app/modules/alerts/models/`。本图不描绘可选机器学习或未验证品质概率。

## 复现与实际检查

现有 XeLaTeX + Windows Microsoft YaHei，禁自动安装和 shell escape：

```powershell
xelatex -disable-installer -no-shell-escape -interaction=nonstopmode -halt-on-error tikz_system_architecture.tex
xelatex -disable-installer -no-shell-escape -interaction=nonstopmode -halt-on-error tikz_core_er.tex
```

初次因 xeCJK 扩展缺失失败，未安装；改为已有 fontspec 加系统字体后两张均成功。
首轮 ER 长表名溢出，已分行；修复通知断词、设备消息双向性后，最终日志无
Overfull/Missing character。两张最终 PDF 各一页，Poppler150DPI渲染后均完整打开查看，
无缺字、遮挡或裁切。参考技能的 TikZ 几何/路径检查均为0 CRITICAL；字号估算提示
仍保留（检查器不理解样式继承/分行），以真实字号/日志和实际看图核对，不伪称零提示。
`latex_includes.tex` 两个引用与两张产物对齐；本任务没有完整论文，不宣称论文排版验收。

执行环境只使用现有 TeX/Poppler 和 Codex 随附工具，未安装或卸载宿主 Python 包。
辅助技能文件、编译日志及失败中间件在私有 work 保留，不随源码材料图发布。
