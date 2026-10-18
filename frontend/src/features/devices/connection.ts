import type { Device } from "./api"

type Connection = Pick<Device, "connectivity" | "last_seen_at">

// Only the server decides liveness. Do not infer online from registration,
// temperature values, or a browser timer. Invalid/incomplete responses fail
// closed rather than presenting a positive connection state.
export function connectionLabel(device: Connection): string {
  if (
    !device.last_seen_at ||
    !Number.isFinite(Date.parse(device.last_seen_at))
  ) {
    return "未连接 / 未知"
  }
  if (device.connectivity === "online") return "在线"
  if (device.connectivity === "offline") return "离线"
  return "未连接 / 未知"
}

export function formatDeviceTime(value: string | null): string {
  if (!value) return "尚无记录"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间记录无效"
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date)
}
