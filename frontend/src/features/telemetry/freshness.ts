const MAX_OBSERVATION_AGE_MS = 60_000
const MAX_FUTURE_SKEW_MS = 5_000
const EXPLICIT_UTC =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/

export function isLatestObservationStale(
  checkedAt: string,
  receivedAt: string,
  fetchedAtMs: number,
  nowMs: number,
): boolean {
  if (!EXPLICIT_UTC.test(checkedAt) || !EXPLICIT_UTC.test(receivedAt)) {
    return true
  }
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
