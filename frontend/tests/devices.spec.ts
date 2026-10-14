import { expect, type Page, test } from "@playwright/test"
import type { Device } from "../src/features/devices/api"

const device: Device = {
  id: "11111111-1111-4111-8111-111111111111",
  code: "ui-fixture-temp",
  name: "界面测试测点",
  kind: "temperature_sensor",
  warehouse_id: null,
  is_simulated: true,
  created_at: "2026-10-10T00:00:00Z",
  last_seen_at: null,
  connectivity: "unknown",
}

async function mockList(page: Page, data: Device[] = []) {
  await page.route("**/api/v1/devices/**", async (route) => {
    await route.fulfill({ json: { data, count: data.length } })
  })
}

// Only reads the real API. All fault/permission tests below use intercepted
// responses and do not create accounts/devices or change the local database.
test("Devices live API list matches the page count", async ({ page }) => {
  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/devices/" &&
      response.request().method() === "GET",
  )
  await page.goto("/devices")
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const result = await response.json()
  await expect(
    page.getByRole("heading", { name: `设备列表（共 ${result.count} 台）` }),
  ).toBeVisible()
  if (result.data.length === 0) {
    await expect(page.getByText("暂无设备，请管理员先注册。")).toBeVisible()
  } else {
    await expect(
      page.getByText(result.data[0].code, { exact: true }),
    ).toBeVisible()
  }
})

test("Devices UI mock: empty list is not an online state", async ({ page }) => {
  await mockList(page)
  await page.goto("/devices")
  await expect(page.getByText("暂无设备，请管理员先注册。")).toBeVisible()
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "上一页" })).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "注册设备", exact: true }),
  ).toBeDisabled()
})

test("Devices UI mock: ordinary user has no registration form", async ({
  page,
}) => {
  await page.route("**/api/v1/users/me", async (route) => {
    await route.fulfill({
      json: {
        id: "22222222-2222-4222-8222-222222222222",
        email: "readonly-fixture@example.com",
        full_name: "Read only fixture",
        is_active: true,
        is_superuser: false,
      },
    })
  })
  await mockList(page, [device])
  await page.goto("/devices")
  await expect(
    page.getByText("当前为只读访问；管理员可注册设备。"),
  ).toBeVisible()
  await expect(page.getByLabel("设备编号")).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "注册设备", exact: true }),
  ).toHaveCount(0)
  await expect(page.getByText(device.code, { exact: true })).toBeVisible()
})

test("Devices UI mock: duplicate refusal does not increase count", async ({
  page,
}) => {
  await page.route("**/api/v1/devices/**", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 409,
        json: { detail: "Device code already exists" },
      })
    } else {
      await route.fulfill({ json: { data: [device], count: 1 } })
    }
  })
  await page.goto("/devices")
  await page.getByLabel("设备编号").fill(device.code)
  await page.getByLabel("设备名称").fill("重复测点")
  await page.getByRole("button", { name: "注册设备", exact: true }).click()
  await expect(page.getByRole("alert")).toHaveText(
    "设备编号已存在，请换一个编号。",
  )
  await expect(
    page.getByRole("heading", { name: "设备列表（共 1 台）" }),
  ).toBeVisible()
  await expect(page.getByText("已注册 重复测点", { exact: false })).toHaveCount(
    0,
  )
})

test("Devices UI mock: server denies registration without success", async ({
  page,
}) => {
  await page.route("**/api/v1/devices/**", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 403, json: { detail: "Forbidden" } })
    } else {
      await route.fulfill({ json: { data: [], count: 0 } })
    }
  })
  await page.goto("/devices")
  await page.getByLabel("设备编号").fill("forbidden-fixture")
  await page.getByLabel("设备名称").fill("拒绝测点")
  await page.getByRole("button", { name: "注册设备", exact: true }).click()
  await expect(page.getByRole("alert")).toHaveText(
    "当前账号没有注册设备的权限。",
  )
  await expect(
    page.getByRole("heading", { name: "设备列表（共 0 台）" }),
  ).toBeVisible()
  await expect(page).toHaveURL(/\/devices$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).not.toBeNull()
})

test("Devices UI mock: missing authentication returns to login", async ({
  page,
}) => {
  await page.route("**/api/v1/devices/**", async (route) => {
    await route.fulfill({ status: 401, json: { detail: "Not authenticated" } })
  })
  await page.goto("/devices")
  await expect(page).toHaveURL(/\/login$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
})

test("Devices UI mock: expired JWT on refresh returns to login", async ({
  page,
}) => {
  await mockList(page, [device])
  await page.goto("/devices")
  await expect(page.getByText(device.code, { exact: true })).toBeVisible()
  await page.route("**/api/v1/devices/**", async (route) => {
    await route.fulfill({
      status: 403,
      json: { detail: "Could not validate credentials" },
    })
  })
  await page.getByRole("button", { name: "刷新", exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
})

test("Devices UI mock: superuser self-delete refusal keeps the session", async ({
  page,
}) => {
  await page.route("**/api/v1/users/me", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({
        status: 403,
        json: { detail: "Super users are not allowed to delete themselves" },
      })
    } else {
      await route.continue()
    }
  })
  await page.goto("/settings")
  await page.getByRole("tab", { name: "Danger zone" }).click()
  await page
    .getByRole("button", { name: "Delete Account", exact: true })
    .click()
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Delete", exact: true })
    .click()
  await expect(
    page.getByText("Super users are not allowed to delete themselves", {
      exact: true,
    }),
  ).toBeVisible()
  await expect(page).toHaveURL(/\/settings$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).not.toBeNull()
})

test("Devices UI mock: inactive current user returns to login", async ({
  page,
}) => {
  await page.route("**/api/v1/users/me", async (route) => {
    await route.fulfill({ status: 400, json: { detail: "Inactive user" } })
  })
  await mockList(page)
  await page.goto("/devices")
  await expect(page).toHaveURL(/\/login$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
})

test("Devices UI mock: network failure is explicit and refresh recovers", async ({
  page,
}) => {
  let unavailable = true
  await page.route("**/api/v1/devices/**", async (route) => {
    if (unavailable) await route.abort("failed")
    else await route.fulfill({ json: { data: [device], count: 1 } })
  })
  await page.goto("/devices")
  await expect(page.getByRole("alert")).toContainText("无法连接服务", {
    timeout: 20000,
  })
  await expect(page.getByText(device.code, { exact: true })).toHaveCount(0)
  unavailable = false
  await page.getByRole("button", { name: "刷新", exact: true }).click()
  await expect(page.getByText(device.code, { exact: true })).toBeVisible()
  await expect(page.getByRole("alert")).toHaveCount(0)
})

test("Devices UI mock: missing detail is not a fabricated device", async ({
  page,
}) => {
  await page.route("**/api/v1/devices/**", async (route) => {
    if (new URL(route.request().url()).pathname.endsWith(device.id)) {
      await route.fulfill({ status: 404, json: { detail: "Device not found" } })
    } else {
      await route.fulfill({ json: { data: [device], count: 1 } })
    }
  })
  await page.goto("/devices")
  await page.getByRole("button", { name: "查看详情" }).click()
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "设备不存在，请刷新列表。",
    { timeout: 20000 },
  )
  await expect(
    page.getByRole("dialog").getByText("设备 ID", { exact: true }),
  ).toHaveCount(0)
})

test("Devices UI mock: detail keeps unknown heartbeat and Beijing time", async ({
  page,
}) => {
  await page.route("**/api/v1/devices/**", async (route) => {
    if (new URL(route.request().url()).pathname.endsWith(device.id)) {
      await route.fulfill({ json: device })
    } else {
      await route.fulfill({ json: { data: [device], count: 1 } })
    }
  })
  await page.goto("/devices")
  await page.getByRole("button", { name: "查看详情" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByText(device.id, { exact: true })).toBeVisible()
  await expect(dialog.getByText("未连接 / 未知", { exact: true })).toBeVisible()
  await expect(dialog.getByText("尚无记录", { exact: true })).toBeVisible()
  await expect(dialog.getByText(/2026.*08:00:00/)).toBeVisible()
})
