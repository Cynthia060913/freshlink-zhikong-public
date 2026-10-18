import { describe, expect, test } from "bun:test"
import {
  connectionLabel,
  formatDeviceTime,
} from "../src/features/devices/connection"

describe("server-provided connectivity", () => {
  const timestamp = "2026-10-14T00:00:00Z"
  test("registration and legacy timestamp never imply online", () => {
    expect(
      connectionLabel({ connectivity: "unknown", last_seen_at: null }),
    ).toBe("未连接 / 未知")
    expect(
      connectionLabel({ connectivity: "unknown", last_seen_at: timestamp }),
    ).toBe("未连接 / 未知")
  })
  test("online requires a real recorded timestamp", () => {
    expect(
      connectionLabel({ connectivity: "online", last_seen_at: timestamp }),
    ).toBe("在线")
    expect(
      connectionLabel({ connectivity: "online", last_seen_at: null }),
    ).toBe("未连接 / 未知")
  })
  test("offline is distinct from never-seen", () => {
    expect(
      connectionLabel({ connectivity: "offline", last_seen_at: timestamp }),
    ).toBe("离线")
  })
  test("bad timestamps cannot create positive connection status", () => {
    expect(
      connectionLabel({ connectivity: "online", last_seen_at: "not-a-time" }),
    ).toBe("未连接 / 未知")
  })
  test("Beijing display handles UTC equivalence and malformed data safely", () => {
    expect(formatDeviceTime(timestamp)).toContain("08:00:00")
    expect(formatDeviceTime(timestamp)).toBe(
      formatDeviceTime("2026-10-14T08:00:00+08:00"),
    )
    expect(formatDeviceTime(null)).toBe("尚无记录")
    expect(formatDeviceTime("not-a-time")).toBe("时间记录无效")
  })
})
