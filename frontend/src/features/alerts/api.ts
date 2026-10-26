import { isAxiosError } from "axios"
import { client } from "@/client/client.gen"

export type AlertState = "open" | "acknowledged" | "resolved"

// Only events that OPEN an alert appear in this list; recovery
// candidates and device lifecycle records are excluded server-side.
export interface AlertRow {
  alert_event_id: string
  source_event_id: string
  related_source_event_id: string | null
  kind: string
  severity: string | null
  warehouse_id: string | null
  batch_id: string | null
  device_id: string | null
  metric_code: string | null
  sampled_at: string
  value: number | null
  unit: string | null
  related_source_event_id: string | null
  status: AlertState
  created_at: string
}
export interface AlertPage {
  data: AlertRow[]
  count: number
}

export interface AlertTransition {
  id: string
  alert_event_id: string
  from_status: AlertState
  to_status: AlertState
  trigger_source_event_id: string | null
  reason: string | null
  created_at: string
}
export interface AlertLifecycle {
  alert_event_id: string
  status: AlertState
  transition_created: boolean
  transitions: AlertTransition[]
}

export interface AlertQuery {
  state?: AlertState
  severity?: string
}

// Reuse the template's configured bearer client. Pages never handle tokens or HTTP.
export const alertsApi = {
  async list(
    skip: number,
    limit: number,
    query: AlertQuery,
  ): Promise<AlertPage> {
    const response = await client.get<
      { 200: AlertPage },
      { 422: unknown },
      true
    >({
      url: "/api/v1/alerts/",
      query: {
        skip,
        limit,
        ...(query.state ? { state: query.state } : {}),
        ...(query.severity ? { severity: query.severity } : {}),
      },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async lifecycle(alertEventId: string): Promise<AlertLifecycle> {
    const response = await client.get<
      { 200: AlertLifecycle },
      { 404: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/alerts/{alert_event_id}/status",
      path: { alert_event_id: alertEventId },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async acknowledge(alertEventId: string): Promise<AlertLifecycle> {
    const response = await client.post<
      { 200: AlertLifecycle },
      { 403: unknown; 404: unknown; 409: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/alerts/{alert_event_id}/acknowledge",
      path: { alert_event_id: alertEventId },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
}

export const stateLabels: Record<AlertState, string> = {
  open: "待处理",
  acknowledged: "已确认",
  resolved: "已恢复（机器判定）",
}

export function alertTime(value: string | null): string {
  if (!value) return "无记录"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间记录无效"
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date)
}

export function alertError(error: unknown): string {
  if (isAxiosError(error)) {
    if (error.response?.status === 403)
      return "当前账号没有确认告警的权限；确认操作仅限管理员。"
    if (error.response?.status === 404) return "告警不存在，请刷新列表。"
    if (error.response?.status === 409)
      return "该告警已恢复，不能再确认；历史记录未被修改。"
    if (error.response?.status === 422) return "查询条件或告警字段不符合要求。"
    if (error.response?.status === 503)
      return "服务暂时不可用，请稍后重试；失败不代表没有告警。"
    if (!error.response) return "无法连接服务，请确认本机应用正在运行。"
  }
  return "操作失败，请重试；持续失败请保留错误供排查。"
}
