import { isAxiosError } from "axios"
import { client } from "@/client/client.gen"

export interface TelemetryFilters {
  device_id?: string
  warehouse_id?: string
  sampled_from?: string
  sampled_to?: string
  received_from?: string
  received_to?: string
  quality_in?: string[]
}

export interface TelemetryItem {
  observation_id: string
  device_id: string
  warehouse_id: string
  sampled_at: string | null
  received_at: string
  temperature_c: number | null
  humidity_percent: number | null
  quality: string[]
  risk_state: string
  rule_version: string | null
}

export interface TelemetryPageResult {
  snapshot_id: string
  items: TelemetryItem[]
  total: number
  next_cursor: string | null
}

export interface TelemetryLatestResult {
  item: TelemetryItem | null
  checked_at: string
}

export interface TelemetryDetail {
  observation_id: string
  raw_text: string
  normalized: unknown
  quality: string[]
  risk: unknown
  reception_reuse_conflict_count: number
  receptions: Array<{
    reception_id: string
    received_at: string
    outcome: string
    reason: string | null
  }>
}

function query(filters: TelemetryFilters): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) =>
      Array.isArray(value) ? value.length > 0 : Boolean(value),
    ),
  )
}

export const telemetryApi = {
  async latest(filters: TelemetryFilters): Promise<TelemetryLatestResult> {
    const response = await client.get<
      { 200: TelemetryLatestResult },
      { 401: unknown; 422: unknown; 503: unknown },
      true
    >({
      url: "/api/v1/telemetry/observations/latest",
      query: {
        device_id: filters.device_id,
        warehouse_id: filters.warehouse_id,
      },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async page(
    filters: TelemetryFilters,
    snapshotId?: string,
    cursor?: string,
  ): Promise<TelemetryPageResult> {
    const response = await client.get<
      { 200: TelemetryPageResult },
      { 401: unknown; 422: unknown; 429: unknown; 503: unknown },
      true
    >({
      url: "/api/v1/telemetry/observations/",
      query: { ...query(filters), snapshot_id: snapshotId, cursor, limit: 20 },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async detail(observationId: string): Promise<TelemetryDetail> {
    const response = await client.get<
      { 200: TelemetryDetail },
      { 401: unknown; 404: unknown; 503: unknown },
      true
    >({
      url: "/api/v1/telemetry/observations/{observation_id}",
      path: { observation_id: observationId },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async csv(filters: TelemetryFilters, snapshotId: string): Promise<Blob> {
    const response = await client.get<
      { 200: Blob },
      { 401: unknown; 422: unknown; 503: unknown },
      true
    >({
      url: "/api/v1/telemetry/observations/export.csv",
      query: { ...query(filters), snapshot_id: snapshotId },
      responseType: "blob",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
}

export function telemetryError(error: unknown): string {
  if (isAxiosError(error)) {
    if (error.response?.status === 401 || error.response?.status === 403)
      return "请重新登录后查看遥测数据。"
    if (error.response?.status === 404) return "观测不存在，请重新查询。"
    if (error.response?.status === 422)
      return "筛选条件或快照已失效，请重新查询。"
    if (error.response?.status === 429)
      return "当前查询快照数量已满，请稍后重试。"
    if (error.response?.status === 503)
      return "数据服务暂时不可用，请稍后重试；当前状态不能判定正常。"
    if (!error.response) return "无法连接数据服务，请检查应用连接。"
  }
  return "查询失败；当前状态不能判定正常。"
}
