import { isAxiosError } from "axios"
import { client } from "@/client/client.gen"

export type DeviceKind = "temperature_sensor" | "cooling_actuator"
export interface Device {
  id: string
  code: string
  name: string
  kind: DeviceKind
  warehouse_id: string | null
  is_simulated: boolean
  created_at: string
  last_seen_at: string | null
  connectivity: "unknown"
}
export interface DeviceInput {
  code: string
  name: string
  kind: DeviceKind
}
export interface DevicePage {
  data: Device[]
  count: number
}

// Reuse the template's configured bearer client. Pages never handle tokens or HTTP.
export const devicesApi = {
  async list(skip: number, limit: number): Promise<DevicePage> {
    const response = await client.get<
      { 200: DevicePage },
      { 422: unknown },
      true
    >({
      url: "/api/v1/devices/",
      query: { skip, limit },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async detail(id: string): Promise<Device> {
    const response = await client.get<
      { 200: Device },
      { 404: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/devices/{device_id}",
      path: { device_id: id },
      responseType: "json",
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
  async register(body: DeviceInput): Promise<Device> {
    const response = await client.post<
      { 201: Device },
      { 403: unknown; 409: unknown; 422: unknown },
      true
    >({
      url: "/api/v1/devices/",
      body,
      responseType: "json",
      headers: { "Content-Type": "application/json" },
      security: [{ scheme: "bearer", type: "http" }],
      throwOnError: true,
    })
    return response.data
  },
}

export function deviceError(error: unknown): string {
  if (isAxiosError(error)) {
    if (error.response?.status === 409) return "设备编号已存在，请换一个编号。"
    if (error.response?.status === 403) return "当前账号没有注册设备的权限。"
    if (error.response?.status === 404) return "设备不存在，请刷新列表。"
    if (error.response?.status === 422)
      return "设备字段不符合要求，请检查编号、名称和类型。"
    if (!error.response) return "无法连接服务，请确认本机应用正在运行。"
  }
  return "操作失败，请重试；持续失败请保留错误供排查。"
}

export function deviceTime(value: string | null): string {
  if (!value) return "尚无记录"
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value))
}
