import { isAxiosError } from "axios"
import { client } from "@/client/client.gen"

export type CommandStatus =
  | "queued"
  | "sent"
  | "acknowledged"
  | "failed"
  | "timeout"

export interface CommandInput {
  command_id: string
  action: "set_cooling"
  enabled: boolean
  lifetime_seconds: number
  event_id: null
}

export interface Command {
  command_id: string
  device_id: string
  warehouse_id: string
  action: "set_cooling"
  enabled: boolean
  issued_at: string
  expires_at: string
  status: CommandStatus
  attempt_count: number
  transport_accepted_at: string | null
  ack_received_at: string | null
  terminal_at: string | null
  failure_reason: string | null
  event_id: null
  parameters: { enabled: boolean }
}

export interface Receipt {
  received_at: string
  executed_at: string | null
  result: "executed" | "failed"
  cooling_enabled: boolean
  reason: string | null
  outcome: "accepted" | "late" | "conflict"
}

export interface CommandDetail extends Command {
  receipts: Receipt[]
}

export const commandLabels: Record<CommandStatus, string> = {
  queued: "排队中（未执行）",
  sent: "已发送（待设备回执）",
  acknowledged: "设备回执：执行成功",
  failed: "失败（未确认执行成功）",
  timeout: "超时（不代表执行成功）",
}

export const isTerminal = (status: CommandStatus) =>
  status === "acknowledged" || status === "failed" || status === "timeout"

export const controlApi = {
  async list(deviceId: string): Promise<{ data: Command[]; count: number }> {
    const response = await client.get<
      { 200: { data: Command[]; count: number } },
      { 404: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/devices/{device_id}/commands",
      path: { device_id: deviceId },
      query: { limit: 5 },
      timeout: 10000,
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async detail(deviceId: string, commandId: string): Promise<CommandDetail> {
    const response = await client.get<
      { 200: CommandDetail },
      { 404: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/devices/{device_id}/commands/{command_id}",
      path: { device_id: deviceId, command_id: commandId },
      timeout: 10000,
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async create(deviceId: string, body: CommandInput): Promise<Command> {
    const response = await client.post<
      { 201: Command },
      { 403: unknown; 404: unknown; 409: unknown; 422: unknown; 503: unknown },
      true
    >({
      url: "/api/v1/devices/{device_id}/commands",
      path: { device_id: deviceId },
      body,
      timeout: 10000,
      responseType: "json",
      headers: { "Content-Type": "application/json" },
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
}

// Only explain allowlisted server reason codes; never echo arbitrary server text.
export function controlError(error: unknown): string {
  if (isAxiosError(error)) {
    const status = error.response?.status
    const reason = error.response?.data?.detail?.reason_code
    if (status === 503 && reason === "warehouse_service_not_integrated")
      return "仓储服务尚未接线，不能下发控制；本次未确认受理。"
    if (status === 403) return "当前账号没有下发控制的权限。"
    if (status === 404)
      return "暂未查到该设备或命令；发送结果未明时仍须使用原编号查询或重试。"
    if (status === 409)
      return "控制条件冲突，请核对设备、绑定及已有命令；不能据此判定执行成功。"
    if (status === 429) return "命令容量已满，请稍后查询或使用原编号重试。"
    if (status === 422) return "控制参数未被接受，请保留原编号核对。"
    if (!error.response || status === 503)
      return "连接或存储暂不可用，发送结果未确认；不要生成新编号重复下发。"
  }
  return "命令操作未确认，请查询原编号；不能把请求完成当作设备执行。"
}
