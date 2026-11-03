import type { TelemetryLatestResult } from "../telemetry/api"
import type { ForecastPreview } from "./forecast-api"

export const OUTCOME_SAMPLE_TOLERANCE_MS = 5_000
export const OUTCOME_WAIT_MS = 30_000

// A prospective single-case comparison, not a retrospective forecast or a
// model-performance score. Keep the originally captured prediction unchanged.
export function matchForecastOutcome(
  forecast: ForecastPreview,
  latest: TelemetryLatestResult,
) {
  const item = latest.item
  const target = Date.parse(forecast.target_at ?? "")
  const sampled = Date.parse(item?.sampled_at ?? "")
  const received = Date.parse(item?.received_at ?? "")
  const checked = Date.parse(latest.checked_at)
  if (
    forecast.status !== "predicted" ||
    forecast.predicted_temperature_c === null ||
    !Number.isFinite(forecast.predicted_temperature_c) ||
    !item ||
    item.device_id !== forecast.device_id ||
    item.warehouse_id !== forecast.warehouse_id ||
    item.quality.length !== 1 ||
    item.quality[0] !== "ok" ||
    item.temperature_c === null ||
    !Number.isFinite(item.temperature_c) ||
    ![target, sampled, received, checked].every(Number.isFinite) ||
    sampled < target ||
    sampled > target + OUTCOME_SAMPLE_TOLERANCE_MS ||
    received < sampled ||
    received > checked ||
    checked > target + OUTCOME_WAIT_MS ||
    forecast.input_points.some(
      (point) => point.observation_id === item.observation_id,
    )
  )
    return null
  return {
    observation: item,
    offsetSeconds: (sampled - target) / 1_000,
    absoluteDifferenceC: Math.abs(
      forecast.predicted_temperature_c - item.temperature_c,
    ),
  }
}
