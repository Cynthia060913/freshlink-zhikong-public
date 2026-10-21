const MAX_OBSERVATION_AGE_MS = 60_000
const MAX_FUTURE_SKEW_MS = 5_000

export function isLatestObservationStale(
  checkedAt: string,
  receivedAt: string,
  fetchedAtMs: number,
  nowMs: number,
): boolean {
  const serverAgeMs = Date.parse(checkedAt) - Date.parse(receivedAt)
  const elapsedMs = nowMs - fetchedAtMs
  if (
    !Number.isFinite(serverAgeMs) ||
    !Number.isFinite(elapsedMs) ||
    fetchedAtMs <= 0 ||
    elapsedMs < 0 ||
    serverAgeMs < -MAX_FUTURE_SKEW_MS
  ) {
    return true
  }
  return serverAgeMs + elapsedMs > MAX_OBSERVATION_AGE_MS
}
