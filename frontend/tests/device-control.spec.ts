import { expect, type Page, test } from "@playwright/test"
import type { Device } from "../src/features/devices/api"
import type {
  CommandDetail,
  CommandInput,
  CommandStatus,
} from "../src/features/devices/control-api"

// UI boundary tests intercept every relevant API. They do not control a real
// device, send notifications, or prove B/C/MQTT/database integration.
test.use({ storageState: { cookies: [], origins: [] } })
const actorId = "22222222-2222-4222-8222-222222222222"
const commandId = "33333333-3333-4333-8333-333333333333"
const actuator: Device = {
  id: "11111111-1111-4111-8111-111111111111",
  code: "fixture-cooling",
  name: "界面测试制冷执行器",
  kind: "cooling_actuator",
  warehouse_id: "44444444-4444-4444-8444-444444444444",
  is_simulated: true,
  created_at: "2026-10-15T00:00:00Z",
  last_seen_at: "2026-10-15T00:00:00Z",
  connectivity: "online",
}
function command(status: CommandStatus, id = commandId): CommandDetail {
  return {
    command_id: id,
    device_id: actuator.id,
    warehouse_id: actuator.warehouse_id!,
    action: "set_cooling",
    enabled: true,
    parameters: { enabled: true },
    event_id: null,
    issued_at: "2026-10-15T00:00:00Z",
    expires_at: "2026-10-15T00:01:00Z",
    status,
    attempt_count: status === "queued" ? 0 : 1,
    transport_accepted_at: status === "queued" ? null : "2026-10-15T00:00:01Z",
    ack_received_at: status === "acknowledged" ? "2026-10-15T00:00:02Z" : null,
    terminal_at: ["acknowledged", "failed", "timeout"].includes(status)
      ? "2026-10-15T00:01:00Z"
      : null,
    failure_reason:
      status === "timeout"
        ? "expired"
        : status === "failed"
          ? "simulated_switch_failure"
          : null,
    receipts: [],
  }
}

async function fixture(
  page: Page,
  options: {
    device?: Device
    admin?: boolean
    initial?: CommandDetail
    listUnavailable?: boolean
    postStatus?: number
    networkFailure?: boolean
    acceptedButResponseLost?: boolean
  } = {},
) {
  const device = options.device ?? actuator
  const requests: CommandInput[] = []
  let current = options.initial ?? null
  let networkFailure = options.networkFailure ?? false
  await page.addInitScript(() =>
    localStorage.setItem(
      "access_token",
      "synthetic-ui-fixture-not-a-credential",
    ),
  )
  await page.route("**/api/v1/users/me", async (route) =>
    route.fulfill({
      json: {
        id: actorId,
        email: "fixture@example.com",
        full_name: "UI fixture",
        is_active: true,
        is_superuser: options.admin ?? true,
      },
    }),
  )
  await page.route("**/api/v1/devices/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith("/connectivity-events")) {
      await route.fulfill({ json: { data: [], count: 0 } })
    } else if (path.includes("/commands/")) {
      await route.fulfill(
        current
          ? { json: current }
          : { status: 404, json: { detail: "Command not found" } },
      )
    } else if (path.endsWith("/commands") && request.method() === "POST") {
      const body = request.postDataJSON() as CommandInput
      requests.push(body)
      if (options.acceptedButResponseLost) {
        current = command("queued", body.command_id)
        await route.abort("failed")
      } else if (networkFailure) {
        await route.abort("failed")
      } else if (options.postStatus) {
        await route.fulfill({
          status: options.postStatus,
          json: {
            detail: {
              reason_code:
                options.postStatus === 503
                  ? "warehouse_service_not_integrated"
                  : "control_forbidden",
            },
          },
        })
      } else {
        current = {
          ...command("queued", body.command_id),
          enabled: body.enabled,
          parameters: { enabled: body.enabled },
        }
        await route.fulfill({ status: 201, json: current })
      }
    } else if (path.endsWith("/commands")) {
      await route.fulfill(
        options.listUnavailable
          ? {
              status: 503,
              json: { detail: { reason_code: "storage_unavailable" } },
            }
          : {
              json: { data: current ? [current] : [], count: current ? 1 : 0 },
            },
      )
    } else if (path.endsWith(device.id)) {
      await route.fulfill({ json: device })
    } else {
      await route.fulfill({ json: { data: [device], count: 1 } })
    }
  })
  return {
    requests,
    recoverNetwork: () => {
      networkFailure = false
    },
    setCurrent: (value: CommandDetail) => {
      current = value
    },
  }
}

async function openPanel(page: Page) {
  await page.goto("/devices")
  await page.getByRole("button", { name: "查看详情" }).click()
  const panel = page.getByRole("region", { name: "远程制冷控制" })
  await expect(panel).toBeVisible()
  return panel
}
async function submit(page: Page) {
  await page.getByRole("button", { name: "准备下发", exact: true }).click()
  await page
    .getByRole("button", { name: "确认下发开启制冷", exact: true })
    .click()
}

test("Control UI mock: preparation/cancellation never sends a command", async ({
  page,
}) => {
  const state = await fixture(page)
  const panel = await openPanel(page)
  await panel.getByRole("button", { name: "准备下发" }).click()
  await expect(
    panel.getByRole("group", { name: "确认控制命令" }),
  ).toContainText(actuator.code)
  await panel
    .getByRole("group", { name: "确认控制命令" })
    .scrollIntoViewIfNeeded()
  await page.screenshot({
    path: test.info().outputPath("control-confirmation-desktop.png"),
  })
  expect(state.requests).toHaveLength(0)
  await panel.getByRole("button", { name: "取消下发" }).click()
  await expect(panel.getByRole("group", { name: "确认控制命令" })).toHaveCount(
    0,
  )
  expect(state.requests).toHaveLength(0)
})

test("Control UI mock: confirmed action/TTL queued is not execution", async ({
  page,
}) => {
  const state = await fixture(page)
  const panel = await openPanel(page)
  await page.getByLabel("控制动作").selectOption("off")
  await page.getByLabel("命令有效期").selectOption("120")
  await panel.getByRole("button", { name: "准备下发" }).click()
  await expect(
    panel.getByRole("group", { name: "确认控制命令" }),
  ).toContainText("关闭制冷，有效期 120 秒")
  await panel.getByRole("button", { name: "确认下发关闭制冷" }).click()
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "排队中（未执行）",
  )
  expect(state.requests).toHaveLength(1)
  expect(state.requests[0]).toMatchObject({
    action: "set_cooling",
    enabled: false,
    lifetime_seconds: 120,
    event_id: null,
  })
  expect(state.requests[0].command_id).toMatch(/^[0-9a-f-]{36}$/)
  await expect(
    panel.getByText("设备回执：执行成功", { exact: true }),
  ).toHaveCount(0)
  await expect(panel.getByText("暂无设备回执。", { exact: true })).toBeVisible()
})

for (const [status, label] of [
  ["sent", "已发送（待设备回执）"],
  ["acknowledged", "设备回执：执行成功"],
  ["failed", "失败（未确认执行成功）"],
  ["timeout", "超时（不代表执行成功）"],
] as const) {
  test(`Control UI mock: server ${status} is labelled without inference`, async ({
    page,
  }) => {
    await fixture(page, { initial: command(status) })
    const panel = await openPanel(page)
    await panel.getByRole("button", { name: "查询此命令" }).click()
    await expect(panel.getByLabel("命令与回执详情")).toContainText(label)
    if (status !== "acknowledged")
      await expect(
        panel.getByText("设备回执：执行成功", { exact: true }),
      ).toHaveCount(0)
  })
}

test("Control UI mock: timeout stays timeout with late execution receipt", async ({
  page,
}) => {
  const initial = command("timeout")
  initial.receipts = [
    {
      received_at: "2026-10-15T00:01:10Z",
      executed_at: "2026-10-15T00:00:30Z",
      result: "executed",
      cooling_enabled: true,
      reason: null,
      outcome: "late",
    },
  ]
  await fixture(page, { initial })
  const panel = await openPanel(page)
  await panel.getByRole("button", { name: "查询此命令" }).click()
  const detail = panel.getByLabel("命令与回执详情")
  await expect(detail).toContainText("超时（不代表执行成功）")
  await expect(detail).toContainText("迟到回执（不更改原终态）")
  await expect(
    panel.getByText("设备回执：执行成功", { exact: true }),
  ).toHaveCount(0)
})

test("Control UI mock: failed POST/reload/404 retry retains exact ID and body", async ({
  page,
}) => {
  const state = await fixture(page, { networkFailure: true })
  let panel = await openPanel(page)
  await submit(page)
  await expect(
    panel.getByRole("button", { name: "使用原编号重试" }),
  ).toBeVisible()
  const original = state.requests[0]
  await expect(panel.getByRole("button", { name: "准备下发" })).toHaveCount(0)
  await page.reload()
  await page.getByRole("button", { name: "查看详情" }).click()
  panel = page.getByRole("region", { name: "远程制冷控制" })
  await expect(panel).toContainText(original.command_id)
  await expect(panel.getByLabel("命令与回执详情")).toContainText("尚未找到")
  state.recoverNetwork()
  await panel.getByRole("button", { name: "使用原编号重试" }).click()
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "排队中（未执行）",
  )
  expect(state.requests).toHaveLength(2)
  expect(state.requests[1]).toEqual(original)
})

test("Control UI mock: B lookup 503 stays fail-closed, no automatic POST retry", async ({
  page,
}) => {
  const state = await fixture(page, { postStatus: 503 })
  const panel = await openPanel(page)
  await submit(page)
  await expect(
    panel.getByRole("alert").filter({ hasText: "仓储服务尚未接线" }),
  ).toBeVisible()
  await panel.getByRole("button", { name: "查询原编号" }).click()
  await panel.getByRole("button", { name: "刷新命令" }).click()
  expect(state.requests).toHaveLength(1)
  await expect(
    panel.getByText("设备回执：执行成功", { exact: true }),
  ).toHaveCount(0)
})

test("Control UI mock: lost POST response recovers accepted command without another POST", async ({
  page,
}) => {
  const state = await fixture(page, { acceptedButResponseLost: true })
  const panel = await openPanel(page)
  await submit(page)
  await expect(
    panel.getByRole("button", { name: "使用原编号重试" }),
  ).toBeVisible()
  await panel.getByRole("button", { name: "查询原编号" }).click()
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "排队中（未执行）",
  )
  expect(state.requests).toHaveLength(1)
  await expect(panel.getByRole("button", { name: "准备下发" })).toHaveCount(0)
})

test("Control UI mock: only saved terminal status retires the local pending intent", async ({
  page,
}) => {
  const state = await fixture(page)
  const panel = await openPanel(page)
  await submit(page)
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "排队中（未执行）",
  )
  const id = state.requests[0].command_id
  expect(
    await page.evaluate(
      (key) => sessionStorage.getItem(key),
      `freshlink-control-intent:${actorId}:${actuator.id}`,
    ),
  ).not.toBeNull()
  state.setCurrent(command("acknowledged", id))
  await panel.getByRole("button", { name: "刷新命令" }).click()
  await panel.getByRole("button", { name: "查询原编号" }).click()
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "设备回执：执行成功",
  )
  await expect(panel.getByRole("button", { name: "准备下发" })).toBeEnabled()
  expect(
    await page.evaluate(
      (key) => sessionStorage.getItem(key),
      `freshlink-control-intent:${actorId}:${actuator.id}`,
    ),
  ).toBeNull()
  expect(state.requests).toHaveLength(1)
})

test("Control UI mock: write 403 is a refusal, not a logout or execution", async ({
  page,
}) => {
  await fixture(page, { postStatus: 403 })
  const panel = await openPanel(page)
  await submit(page)
  await expect(
    panel.getByRole("alert").filter({ hasText: "当前账号没有下发控制的权限" }),
  ).toBeVisible()
  await expect(page).toHaveURL(/\/devices$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).not.toBeNull()
  await expect(
    panel.getByText("设备回执：执行成功", { exact: true }),
  ).toHaveCount(0)
})

test("Control UI mock: ordinary user can read but cannot prepare a command", async ({
  page,
}) => {
  const state = await fixture(page, {
    admin: false,
    initial: command("acknowledged"),
  })
  const panel = await openPanel(page)
  await expect(panel).toContainText("当前为只读访问")
  await expect(panel.getByRole("button", { name: "准备下发" })).toHaveCount(0)
  await panel.getByRole("button", { name: "查询此命令" }).click()
  await expect(panel.getByLabel("命令与回执详情")).toContainText(
    "设备回执：执行成功",
  )
  expect(state.requests).toHaveLength(0)
})

for (const [label, device] of [
  ["unbound", { ...actuator, warehouse_id: null }],
  ["offline", { ...actuator, connectivity: "offline" as const }],
  ["unknown", { ...actuator, connectivity: "unknown" as const }],
] as const) {
  test(`Control UI mock: ${label} actuator cannot prepare a new command`, async ({
    page,
  }) => {
    const state = await fixture(page, { device })
    const panel = await openPanel(page)
    await expect(panel.getByRole("button", { name: "准备下发" })).toBeDisabled()
    expect(state.requests).toHaveLength(0)
  })
}

test("Control UI mock: sensor never exposes a cooling command", async ({
  page,
}) => {
  await fixture(page, { device: { ...actuator, kind: "temperature_sensor" } })
  const panel = await openPanel(page)
  await expect(panel).toContainText("温度测点不接受制冷控制命令")
  await expect(panel.getByRole("button", { name: "准备下发" })).toHaveCount(0)
})

test("Control UI mock: active command or failed history prevents new intent", async ({
  page,
}) => {
  await fixture(page, { initial: command("queued") })
  const panel = await openPanel(page)
  await expect(panel).toContainText("已有未结束命令")
  await expect(panel.getByRole("button", { name: "准备下发" })).toBeDisabled()
})

test("Control UI mock: history failure is not an empty successful history", async ({
  page,
}) => {
  await fixture(page, { listUnavailable: true })
  const panel = await openPanel(page)
  await expect(panel.getByRole("alert")).toContainText("控制记录查询失败")
  await expect(panel.getByRole("button", { name: "准备下发" })).toBeDisabled()
  await expect(
    panel.getByText("暂无控制记录；不代表设备状态正常。", { exact: true }),
  ).toHaveCount(0)
})

test("Control UI mock: unavailable intent storage refuses POST before sending", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key.startsWith("freshlink-control-intent:"))
        throw new Error("synthetic storage refusal")
      original.call(this, key, value)
    }
  })
  const state = await fixture(page)
  const panel = await openPanel(page)
  await submit(page)
  await expect(panel.getByRole("alert")).toContainText("本次没有发送")
  expect(state.requests).toHaveLength(0)
})

test("Control UI mock: mobile confirmation stays inside viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fixture(page)
  const panel = await openPanel(page)
  await panel.getByRole("button", { name: "准备下发" }).click()
  const confirmation = panel.getByRole("group", { name: "确认控制命令" })
  await confirmation.scrollIntoViewIfNeeded()
  const box = await confirmation.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  await expect(
    panel.getByRole("button", { name: "确认下发开启制冷" }),
  ).toBeVisible()
  await page.screenshot({
    path: test.info().outputPath("control-confirmation-mobile.png"),
  })
})
