# C 短时温度预测：纯计算接口候选

本页只描述可调用的 C 计算模块。无算法保底包保持原字节；本候选尚未接主程序、页面、业务预警或真实通知。`contract-example-only` 参数只验证接口，**不是训练结果或效果证据**。

## 给 A 的最小接点

1. 先使用 A 的 `iot.forecast_window.build_forecast_window`。只有 `status == "ready"` 才允许计算；非 ready 时 C 返回 `unavailable`、原因和空预测，页面不得解释为正常或低风险。
2. 将不可变窗口对象直接传给 `telemetry.forecast_candidate.predict_temperature(window, parameters)`；C 使用结构字段，不需要 A 改自己的类或根路由。`parameters = RidgeParameters.from_json(json.load(...))`。参数 schema 为 `c-ridge-v1`，固定特征顺序、历史120秒、预测目标60秒；参数的真实训练版和哈希待独立实验产生。
3. 返回 `ForecastResult.to_json()`：预测目标为 `as_of + 60s`，附模型版本、参数 SHA256、设备/冷库、输入观测 ID 和首末采样时间。阈值规则、提前关注提示、历史快照和页面由 A/B 在确认后接；C 不写通知或控制。

固定参数结构（以下数值**仅供接口测试**）：

```json
{
  "schema_version": "c-ridge-v1",
  "model_version": "contract-example-only",
  "history_seconds": 120,
  "horizon_seconds": 60,
  "feature_names": ["last_temperature_c", "linear_change_to_target_c", "recent_mean_minus_last_c", "window_range_c"],
  "feature_mean": [0, 0, 0, 0],
  "feature_scale": [1, 1, 1, 1],
  "intercept_c": 0,
  "coefficients": [1, 0, 0, 0]
}
```

可实际运行的固定输入在 `backend/tests/modules/test_forecast_candidate.py::sample`：UTC 决策时刻 `2026-10-31T12:00:00+00:00`，设备 `00000000-0000-0000-0000-000000000001`，冷库 `00000000-0000-0000-0000-000000000002`，8 个 `ready` 观测 ID 从十六进制末尾 `0064` 至 `006b`，采样/接收时刻从 11:59:25 至 12:00:00 每5秒一次，温度从7.0至7.7℃每次加0.1℃。该测试调用上方参数，实际输出如下：

```json
{
  "status": "predicted",
  "reason": null,
  "model_version": "contract-example-only",
  "parameter_sha256": "2b87f8e5404153090d746e3a21b9eb0d3776b31096f3963e06c65c614a81cdf0",
  "device_id": "00000000-0000-0000-0000-000000000001",
  "warehouse_id": "00000000-0000-0000-0000-000000000002",
  "as_of": "2026-10-31T12:00:00+00:00",
  "target_at": "2026-10-31T12:01:00+00:00",
  "predicted_temperature_c": 7.7,
  "input_observation_ids": [
    "00000000-0000-0000-0000-000000000064",
    "00000000-0000-0000-0000-000000000065",
    "00000000-0000-0000-0000-000000000066",
    "00000000-0000-0000-0000-000000000067",
    "00000000-0000-0000-0000-000000000068",
    "00000000-0000-0000-0000-000000000069",
    "00000000-0000-0000-0000-00000000006a",
    "00000000-0000-0000-0000-00000000006b"
  ],
  "input_first_sampled_at": "2026-10-31T11:59:25+00:00",
  "input_last_sampled_at": "2026-10-31T12:00:00+00:00"
}
```

运行：在 `backend` 目录，以项目隔离 Python 执行 `python -m unittest tests.modules.test_forecast_candidate -v`。当前仅纯计算接口测试，真实模型参数与独立效果尚未交付。运行应用仍必须使用项目规定的 Python 3.14；C 在本人环境用隔离的 3.13 对纯标准库模块做了额外兼容检查，不能代替项目同版集成。
