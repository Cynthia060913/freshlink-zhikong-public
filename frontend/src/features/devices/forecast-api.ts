import { client } from "@/client/client.gen"

export interface ForecastPoint {
  observation_id: string
  sampled_at: string
  received_at: string
  temperature_c: number
}

export interface ForecastPreview {
  status: "predicted" | "unavailable"
  reason: string | null
  experimental: true
  business_warning_accepted: false
  device_id: string
  warehouse_id: string | null
  decision_at: string
  target_at: string | null
  refresh_before: string | null
  device_connectivity: string | null
  model_source: string
  model_version: string | null
  parameter_sha256: string | null
  policy_version: string
  input_points: ForecastPoint[]
  predicted_temperature_c: number | null
  last_value_temperature_c: number | null
  linear_trend_temperature_c: number | null
}

export const forecastApi = {
  async capability(): Promise<{ enabled: boolean }> {
    const response = await client.get<
      { 200: { enabled: boolean } },
      { 401: unknown; 403: unknown },
      true
    >({
      url: "/api/v1/devices/forecast-preview/status",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async read(deviceId: string): Promise<ForecastPreview> {
    const response = await client.get<
      { 200: ForecastPreview },
      { 401: unknown; 403: unknown; 404: unknown },
      true
    >({
      url: "/api/v1/devices/{device_id}/temperature-forecast",
      path: { device_id: deviceId },
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
}

const unavailableReasons: Record<string, string> = {
  preview_disabled: "当前版本未开启预测实验。",
  model_unavailable: "模型参数不可用，本次不提供估计。",
  storage_unavailable: "观测读取失败，本次不提供估计。",
  prediction_unavailable: "模型未能产生有效估计。",
  unsupported_device: "预测仅适用于温度测点。",
  unbound_device: "设备尚未绑定冷库。",
  unknown_device: "设备不存在，请刷新设备列表。",
  warehouse_mismatch: "设备绑定已变化，请重新打开详情。",
  input_limit_exceeded: "输入数量超过窗口上限，本次不提供估计。",
  invalid_input: "输入字段不完整或无效。",
  quality_blocked: "近期存在质量不合格的数据，暂不提供估计。",
  insufficient_data: "有效采样数量或时间跨度不足。",
  stale_data: "最近采样已过期，等待新数据。",
  gapped_data: "采样间隔存在缺口，等待连续数据。",
}

export function forecastUnavailable(reason: string | null): string {
  return unavailableReasons[reason ?? ""] ?? "预测暂不可用，请检查采样状态。"
}

export function forecastPlot(snapshot: ForecastPreview) {
  const points = snapshot.input_points
  const target = Date.parse(snapshot.target_at ?? "")
  const predicted = snapshot.predicted_temperature_c
  const start = Date.parse(points[0]?.sampled_at ?? "")
  if (
    snapshot.status !== "predicted" ||
    points.length < 2 ||
    predicted === null ||
    !Number.isFinite(predicted) ||
    !Number.isFinite(start) ||
    !Number.isFinite(target) ||
    target <= start ||
    points.some(
      (point) =>
        !Number.isFinite(point.temperature_c) ||
        !Number.isFinite(Date.parse(point.sampled_at)),
    )
  )
    return null
  const values = [...points.map((point) => point.temperature_c), predicted]
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = Math.max((max - min) * 0.15, 0.15)
  const low = min - padding
  const high = max + padding
  const x = (time: number) => 54 + ((time - start) / (target - start)) * 552
  const y = (temperature: number) =>
    186 - ((temperature - low) / (high - low)) * 162
  return {
    history: points
      .map(
        (point) =>
          `${x(Date.parse(point.sampled_at))},${y(point.temperature_c)}`,
      )
      .join(" "),
    forecastX: x(target),
    forecastY: y(predicted),
    ticks: [low, (low + high) / 2, high].map((value) => ({
      y: y(value),
      label: value.toFixed(1),
    })),
    start,
    target,
  }
}
