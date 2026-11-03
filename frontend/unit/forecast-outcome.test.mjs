import assert from "node:assert/strict"
import { test } from "node:test"
import { matchForecastOutcome } from "../src/features/devices/forecast-outcome.ts"

const target = "2026-11-02T12:00:00Z"
const forecast = {
  status: "predicted",
  target_at: target,
  device_id: "device-a",
  warehouse_id: "room-a",
  predicted_temperature_c: 8.1,
  input_points: [{ observation_id: "old-input" }],
}
const result = () => ({
  checked_at: "2026-11-02T12:00:04Z",
  item: {
    observation_id: "actual-target",
    device_id: "device-a",
    warehouse_id: "room-a",
    sampled_at: "2026-11-02T12:00:02Z",
    received_at: "2026-11-02T12:00:03Z",
    temperature_c: 8.3,
    quality: ["ok"],
  },
})

test("the captured forecast is paired with a later received target observation", () => {
  const pair = matchForecastOutcome(forecast, result())
  assert.equal(pair.observation.observation_id, "actual-target")
  assert.equal(pair.offsetSeconds, 2)
  assert.ok(Math.abs(pair.absoluteDifferenceC - 0.2) < 1e-12)
  assert.equal(forecast.predicted_temperature_c, 8.1)
})
test("old or too-late samples and unreceived future data are not outcome evidence", () => {
  for (const sampled_at of [
    "2026-11-02T11:59:59Z",
    "2026-11-02T12:00:06Z",
    "invalid",
  ]) {
    const latest = result()
    latest.item.sampled_at = sampled_at
    assert.equal(matchForecastOutcome(forecast, latest), null)
  }
  const unreceived = result()
  unreceived.item.received_at = "2026-11-02T12:00:05Z"
  assert.equal(matchForecastOutcome(forecast, unreceived), null)
  const expired = result()
  expired.checked_at = "2026-11-02T12:00:31Z"
  assert.equal(matchForecastOutcome(forecast, expired), null)
})
test("different ownership, bad quality and reused input IDs cannot become outcomes", () => {
  for (const update of [
    { device_id: "device-b" },
    { warehouse_id: "room-b" },
    { quality: ["sensor_error"] },
    { quality: [] },
    { observation_id: "old-input" },
    { temperature_c: NaN },
  ]) {
    const latest = result()
    Object.assign(latest.item, update)
    assert.equal(matchForecastOutcome(forecast, latest), null)
  }
  assert.equal(
    matchForecastOutcome(forecast, { checked_at: target, item: null }),
    null,
  )
})
