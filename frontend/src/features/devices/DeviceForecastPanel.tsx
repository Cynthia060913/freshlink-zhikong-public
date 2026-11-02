import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { type Device, deviceTime } from "./api"
import ForecastOutcomeComparison from "./ForecastOutcomeComparison"
import { forecastApi, forecastPlot, forecastUnavailable } from "./forecast-api"

const shortTime = (timestamp: number) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp)

export default function DeviceForecastPanel({ device }: { device: Device }) {
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(Date.now)
  const capability = useQuery({
    queryKey: ["forecast-preview-capability"],
    queryFn: forecastApi.capability,
    retry: false,
    staleTime: 30_000,
  })
  const forecast = useQuery({
    queryKey: ["device-forecast", device.id],
    queryFn: () => forecastApi.read(device.id),
    enabled: open && capability.data?.enabled === true,
    refetchInterval: open ? 5000 : false,
    retry: false,
  })
  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open])

  if (device.kind !== "temperature_sensor" || capability.data?.enabled !== true)
    return null
  const data = forecast.data
  const expiresAt = Date.parse(data?.refresh_before ?? "")
  const expired = !Number.isFinite(expiresAt) || expiresAt <= now
  const plot = data && !expired ? forecastPlot(data) : null
  return (
    <section className="space-y-3 border-t pt-4" aria-label="短时温度预测实验">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-medium">60秒温度估计 · 实验预览</h3>
        <Button variant="outline" size="sm" onClick={() => setOpen(!open)}>
          {open ? "收起预测" : "查看短时预测"}
        </Button>
      </div>
      {open && (
        <>
          <p className="text-xs text-muted-foreground">
            模型基于仿真数据训练，尚未通过业务预警验收。估计仅供对照观察，
            不判定批次正常，不触发通知或调控；已有阈值告警继续运行。
          </p>
          {forecast.isPending ? (
            <p role="status">正在读取观测并计算…</p>
          ) : forecast.isError ? (
            <div role="alert" className="text-sm text-destructive">
              预测读取失败，已隐藏上次估计。
            </div>
          ) : data?.status !== "predicted" ? (
            <p role="status" className="text-sm">
              {forecastUnavailable(data?.reason ?? null)}
            </p>
          ) : expired ? (
            <p role="status" className="text-sm">
              采样快照已过期，等待刷新后再显示估计。
            </p>
          ) : plot && data ? (
            <>
              <div className="grid grid-cols-3 gap-2 text-center text-sm">
                {[
                  ["岭回归单点估计", data.predicted_temperature_c],
                  ["末值保持对照", data.last_value_temperature_c],
                  ["线性趋势对照", data.linear_trend_temperature_c],
                ].map(([label, value]) => (
                  <div className="rounded border p-2" key={label}>
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 font-semibold">
                      {typeof value === "number"
                        ? `${value.toFixed(2)} °C`
                        : "—"}
                    </div>
                  </div>
                ))}
              </div>
              <figure className="rounded border bg-background p-2">
                <svg
                  viewBox="0 0 640 230"
                  className="w-full"
                  role="img"
                  aria-label="历史采样曲线及未来60秒的一个预测点"
                >
                  <title>采样温度与单点预测</title>
                  {plot.ticks.map((tick) => (
                    <g key={tick.y}>
                      <line
                        x1="54"
                        x2="606"
                        y1={tick.y}
                        y2={tick.y}
                        stroke="#cbd5e1"
                      />
                      <text
                        x="46"
                        y={tick.y + 4}
                        textAnchor="end"
                        fontSize="12"
                        fill="currentColor"
                      >
                        {tick.label}°
                      </text>
                    </g>
                  ))}
                  <line x1="54" x2="606" y1="186" y2="186" stroke="#64748b" />
                  <polyline
                    points={plot.history}
                    fill="none"
                    stroke="#2563eb"
                    strokeWidth="2.5"
                  />
                  <line
                    x1={plot.forecastX}
                    x2={plot.forecastX}
                    y1="24"
                    y2="186"
                    stroke="#9333ea"
                    strokeDasharray="4 4"
                  />
                  <circle
                    data-testid="forecast-point"
                    cx={plot.forecastX}
                    cy={plot.forecastY}
                    r="5"
                    fill="#9333ea"
                  />
                  <text x="54" y="212" fontSize="12" fill="currentColor">
                    {shortTime(plot.start)}
                  </text>
                  <text
                    x="606"
                    y="212"
                    textAnchor="end"
                    fontSize="12"
                    fill="currentColor"
                  >
                    {shortTime(plot.target)}
                  </text>
                </svg>
                <figcaption className="text-xs text-muted-foreground">
                  蓝线：{device.is_simulated ? "软件仿真采样" : "采样温度"}
                  ；紫点：目标时刻的单个估计，非多步预测曲线。
                </figcaption>
              </figure>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt>决策时间</dt>
                <dd>{deviceTime(data.decision_at)}</dd>
                <dt>目标时间</dt>
                <dd>{deviceTime(data.target_at)}</dd>
                <dt>模型版本</dt>
                <dd className="break-all">{data.model_version}</dd>
                <dt>参数标识</dt>
                <dd className="break-all">{data.parameter_sha256}</dd>
                <dt>输入策略</dt>
                <dd>{data.policy_version}</dd>
              </dl>
              <details className="text-xs">
                <summary className="cursor-pointer">
                  本次输入观测（{data.input_points.length}条）
                </summary>
                <ol className="mt-2 max-h-32 space-y-1 overflow-auto">
                  {data.input_points.map((point) => (
                    <li className="break-all" key={point.observation_id}>
                      {deviceTime(point.sampled_at)} ·{" "}
                      {point.temperature_c.toFixed(2)}°C ·{" "}
                      {point.observation_id}
                    </li>
                  ))}
                </ol>
              </details>
            </>
          ) : (
            <p role="status">预测数据格式不可用，未显示估计。</p>
          )}
          <ForecastOutcomeComparison
            device={device}
            snapshot={forecast.isSuccess && !expired && plot ? data! : null}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => forecast.refetch()}
            disabled={forecast.isFetching}
          >
            {forecast.isFetching ? "更新中…" : "刷新预测"}
          </Button>
        </>
      )}
    </section>
  )
}
