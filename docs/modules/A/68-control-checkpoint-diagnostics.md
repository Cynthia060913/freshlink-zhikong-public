# A68：第二机控制后C持久观测失败现场

本轮只改隔离验证脚本，不改业务、前端、迁移、协议、设备模型、25秒等待上限或
“ACK/新规则之后且低于基线0.02°C”的原有断言。不加载任何真实通知配置。

C在main0e第二机的GuardedRecovery两次于wait_for(cooled)失败；设备SQLite有执行
与降温，只能证明设备侧，不能替代C已存观测。本轮尚未定位C失败根因。

## 新增的最小现场

check-gated-telemetry.py在失败时、现有清理前，用只读表查询保存
work/<本轮项目>/control-stored-checkpoint-failure.json：固定阶段、异常类型、
开始/ACK/规则时间、基线和末64个C观测的ID/设备/接收时间/采样时间/温度/quality_ok、
inbox/receipt/observations数量及设备温度/执行次数。不会调用可能驱动投递的stored()。
捕获失败也不会替换原异常、跳过断言或阻止原有清理。

纯helper只输出白名单投影；不写原文、headers、配置、收件人、完整异常文本或SQL响应。
两项标准库实际回归证明：合成秘密字段/错误文本不会进入输出，最多64条；非法ID/时间/
非有限温度不回显。Ruff与格式检查通过。helper保留旧Python可解析的except括号，
仍以已有Python3.14应用镜像作为实际PG/MQTT运行环境，不安装任何依赖。

## A主机实际运行

精确干净工具源 c5195d644046f673e22af7670fff700e1d2fa610，应用镜像与业务源仍是
main 0e508d147517f81dfe2b9245c45c0c0e30076592。实际-GuardedRecovery通过：
真实MQTT/四角色/C01/TCP B恢复、恢复发送器重启不重发、迟到更正关联、控制CLI
executed ACK及C后续观测。控制只执行一次，基线10.003108°C、之后9.976204°C。

结果work/gated-35b7bea12e5e464e9b2b5ac324341de2/runtime-composition.json，SHA256
a241aa0c8ce92a4317d6ceb9c4ded1f6c45c04dda4d7dfb78372139cb12ee0f0。
cleanup.json退出0/清理true；独立按本轮标签查询容器与网络均0。

该A主机通过未触发失败现场路径；不能称C机器已修复，也不能核销C原两次失败。
完整E1、双通知及最终第二机仍不能由本次分段签收。A另一次经本人具体批准的真实
通知试验与本轮无发送隔离验证分开记录，不拼成同一完整E1。

## C下一步仅针对性续验

先只读审查工具差异，保持原工作区；在独立干净候选检出执行同一GuardedRecovery一次，
明确记录工具提交与main0e镜像双来源。若同点失败，保存新增脱敏JSON、结果/清理hash，
以基线/ACK/采样/持久化/队列状态定位首个缺失环节，不刷重跑、不改判定或超时。
若该候选恰好通过，也保留原失败且不宣称找到根因；由A/C比较现场后决定最小修复。
