# 两张设计材料候选图

阶段 figures；非完整论文，不启动整题规划。源码基准为
`be88dea08e843f3d8e52f9d5907cf1d4fcd3ec87`。最终发布前重新核对，不代替业务验收。
原文依据：A-SYSTEM-DESIGN.md、backend/app/main.py、api/main.py、A/B真实模型外键。
Draw.io 不可用；选择 TikZ 的跨层精确连接/实体拓扑路径，非数据图，不使用生成式图像。

<!-- BEGIN FIGURE_MANIFEST -->
## 图表清单（FIGURE_MANIFEST）

**数据图（paper-figure）：**
- none

**DrawIO 确定性技术图（paper-technical-diagram）：**
- none

**TikZ 精确图（paper-technical-diagram）：**
- tikz_system_architecture | claim=候选源码分层与两类未验收通知边界 | source=../A-SYSTEM-DESIGN.md | section=系统架构 | language=zh | format=tex,pdf,png | layout=single-landscape
- tikz_core_er | claim=A/B核心显式外键，不冒充全库最终ER | source=backend/app/modules/iot/control_models.py,backend/app/modules/alerts/models | section=数据库设计 | language=zh | format=tex,pdf,png | layout=single-landscape

**AI 场景图：**
- none

**显式可选格式：**
- none

**总数：** DATA=0, DRAWIO=0, TIKZ=2, ILLUSTRATION=0, OPTIONAL=0, ALL=2
<!-- END FIGURE_MANIFEST -->

原生宽约17cm，引用宽16.5cm，10pt文字约9.7pt。只用青灰主色与浅色/中性白底，
候选以虚线和明确文字表示，不用多色假装进度。PDF和PNG均须实际查看，无不可读裁切。
图例/长限制写正文，不把大段说明焊进图。图不能声称main已发布、C正式迁移或实收。
