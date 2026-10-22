import assert from "node:assert/strict"
import { test } from "node:test"
import { isLatestObservationStale } from "../src/features/telemetry/freshness.ts"

const checkedAt = "2026-10-21T12:00:00Z"
const fetchedAt = Date.parse(checkedAt)

test("a fresh observation ages out even when polling stops", () => {
  const receivedAt = "2026-10-21T11:59:30Z"
  assert.equal(
    isLatestObservationStale(
      checkedAt,
      receivedAt,
      fetchedAt,
      fetchedAt + 30_000,
    ),
    false,
  )
  assert.equal(
    isLatestObservationStale(
      checkedAt,
      receivedAt,
      fetchedAt,
      fetchedAt + 30_001,
    ),
    true,
  )
})

test("already old observations and invalid timestamps remain unknown", () => {
  assert.equal(
    isLatestObservationStale(
      checkedAt,
      "2026-10-21T11:58:59Z",
      fetchedAt,
      fetchedAt,
    ),
    true,
  )
  assert.equal(
    isLatestObservationStale(checkedAt, "bad", fetchedAt, fetchedAt),
    true,
  )
  assert.equal(
    isLatestObservationStale("bad", checkedAt, fetchedAt, fetchedAt),
    true,
  )
})

test("future timestamps and invalid local fetch times fail closed", () => {
  assert.equal(
    isLatestObservationStale(
      checkedAt,
      "2026-10-21T12:00:06Z",
      fetchedAt,
      fetchedAt,
    ),
    true,
  )
  assert.equal(
    isLatestObservationStale(checkedAt, checkedAt, 0, fetchedAt),
    true,
  )
  assert.equal(
    isLatestObservationStale(checkedAt, checkedAt, fetchedAt, fetchedAt - 1),
    true,
  )
})
