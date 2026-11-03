import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { telemetryApi } from "@/features/telemetry/api"
import { type Device, deviceTime } from "./api"
import type { ForecastPreview } from "./forecast-api"
import { matchForecastOutcome, OUTCOME_WAIT_MS } from "./forecast-outcome"

export default function ForecastOutcomeComparison({
  device,
  snapshot,
}: {
  device: Device
  snapshot: ForecastPreview | null
}) {
  const [record, setRecord] = useState<ForecastPreview | null>(null)
  const [pair, setPair] =
    useState<ReturnType<typeof matchForecastOutcome>>(null)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])
  const target = Date.parse(record?.target_at ?? "")
  const deadline = target + OUTCOME_WAIT_MS
  const waitingForTarget = record !== null && now < target
  const expired = record !== null && now > deadline
  const candidate = useQuery({
    queryKey: [
      "forecast-outcome",
      device.id,
      record?.decision_at,
      record?.target_at,
    ],
    queryFn: () =>
      telemetryApi.latest({
        device_id: device.id,
        warehouse_id: record?.warehouse_id ?? undefined,
      }),
    enabled: Boolean(record && !waitingForTarget && !expired && !pair),
    refetchInterval: 2_000,
    retry: false,
  })
  useEffect(() => {
    if (!record || pair || !candidate.isSuccess) return
    const matched = matchForecastOutcome(record, candidate.data)
    if (matched) setPair(matched)
  }, [record, pair, candidate.isSuccess, candidate.data])
  const canCapture =
    snapshot?.status === "predicted" &&
    snapshot.device_id === device.id &&
    snapshot.warehouse_id === device.warehouse_id &&
    snapshot.predicted_temperature_c !== null &&
    Number.isFinite(snapshot.predicted_temperature_c) &&
    Date.parse(snapshot.target_at ?? "") > Date.now() &&
    Date.parse(snapshot.refresh_before ?? "") > Date.now()

  return (
    <section
      className="space-y-3 rounded-md border border-dashed p-3 text-sm"
      aria-label="预测到时对照"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-medium">固定一次预测，到时核对采样</h4>
        <Button
          variant="outline"
          size="sm"
          disabled={!canCapture}
          onClick={() => {
            if (!canCapture || !snapshot) return
            setPair(null)
            setRecord(snapshot)
            setNow(Date.now())
          }}
        >
          {record ? "固定新的预测" : "固定本次预测"}
        </Button>
      </div>
      {!record ? (
        <p className="text-xs text-muted-foreground">
          先固定当前估计；目标时刻到来后，读取同测点目标后5秒内的有效采样进行对照。
        </p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            下列为已固定的历史预测，用于事后核对，不能代表当前温度。对照只保留于本次打开，非整体性能评测。
          </p>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            <dt>固定估计</dt>
            <dd>{record.predicted_temperature_c!.toFixed(2)} °C</dd>
            <dt>决策时间</dt>
            <dd>{deviceTime(record.decision_at)}</dd>
            <dt>目标时间</dt>
            <dd>{deviceTime(record.target_at)}</dd>
          </dl>
          {pair ? (
            <div className="space-y-2" data-testid="forecast-outcome-pair">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded bg-muted/50 p-2">
                  <p className="text-xs text-muted-foreground">对应采样温度</p>
                  <p className="font-semibold">
                    {pair.observation.temperature_c!.toFixed(2)} °C
                  </p>
                </div>
                <div className="rounded bg-muted/50 p-2">
                  <p className="text-xs text-muted-foreground">本次绝对偏差</p>
                  <p className="font-semibold">
                    {pair.absoluteDifferenceC.toFixed(2)} °C
                  </p>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                采样：{deviceTime(pair.observation.sampled_at)}（目标后{" "}
                {pair.offsetSeconds.toFixed(1)} 秒）
              </p>
              <p className="break-all text-xs text-muted-foreground">
                观测标识：{pair.observation.observation_id}
              </p>
              <p className="text-xs text-muted-foreground">
                该偏差对照实际采样值，包含传感器噪声与采样时差；不能据此推算总体准确率或提前预警收益。
              </p>
            </div>
          ) : waitingForTarget ? (
            <p role="status" className="text-xs">
              等待目标时刻，约 {Math.max(0, Math.ceil((target - now) / 1000))}{" "}
              秒；原估计保持不变。
            </p>
          ) : expired ? (
            <p role="status" className="text-xs">
              未在等待期内取得目标后5秒内的有效采样，本次不计算偏差。
            </p>
          ) : candidate.isError ? (
            <p role="status" className="text-xs">
              采样读取失败，仍等待有效对照；尚未计算偏差。
            </p>
          ) : (
            <p role="status" className="text-xs">
              正在等待同一测点、同一冷库、质量有效的目标采样…
            </p>
          )}
        </>
      )}
    </section>
  )
}
