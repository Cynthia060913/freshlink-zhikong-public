import { expect, test } from "@playwright/test"

const first = "11111111-1111-4111-8111-111111111111"
const second = "22222222-2222-4222-8222-222222222222"
const room = "33333333-3333-4333-8333-333333333333"
const device = (id: string, code: string, state: string = "online") => ({
  id,
  code,
  name: code,
  kind: "temperature_sensor",
  warehouse_id: room,
  is_simulated: true,
  created_at: new Date().toISOString(),
  last_seen_at: new Date().toISOString(),
  connectivity: state,
})
const observation = (id: string, value: number) => ({
  observation_id: `observation-${id}`,
  device_id: id,
  warehouse_id: room,
  sampled_at: new Date().toISOString(),
  received_at: new Date().toISOString(),
  temperature_c: value,
  humidity_percent: 70,
  quality: ["ok"],
  risk_state: "unknown",
  rule_version: null,
})

test("Overview UI mock: captured prediction is immutable and compared only after the target sample arrives", async ({
  page,
}) => {
  test.setTimeout(45_000)
  const target = Date.now() + 20_000
  let estimate = 8.1
  await page.route(
    (url) => url.pathname === "/api/v1/devices/",
    (route) =>
      route.fulfill({ json: { data: [device(first, "测点一")], count: 1 } }),
  )
  await page.route("**/api/v1/alerts/**", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/forecast-preview/status", (route) =>
    route.fulfill({ json: { enabled: true } }),
  )
  await page.route("**/temperature-forecast", (route) =>
    route.fulfill({
      json: {
        status: "predicted",
        reason: null,
        experimental: true,
        business_warning_accepted: false,
        device_id: first,
        warehouse_id: room,
        decision_at: new Date(target - 60_000).toISOString(),
        target_at: new Date(target).toISOString(),
        refresh_before: new Date(Date.now() + 10_000).toISOString(),
        model_source: "UI-fixture",
        model_version: "UI-fixture",
        parameter_sha256: "UI-fixture",
        policy_version: "UI-fixture",
        input_points: [180_000, 60_000].map((offset) => ({
          observation_id: `input-${offset}`,
          sampled_at: new Date(target - offset).toISOString(),
          received_at: new Date(target - offset).toISOString(),
          temperature_c: 8.0,
        })),
        predicted_temperature_c: estimate,
        last_value_temperature_c: 8.0,
        linear_trend_temperature_c: 8.0,
      },
    }),
  )
  await page.route("**/observations/latest?**", (route) => {
    const arrived = Date.now() >= target + 1_200
    const item = observation(first, arrived ? 8.3 : 8.0)
    if (arrived) {
      item.sampled_at = new Date(target + 1_000).toISOString()
      item.received_at = new Date(target + 1_200).toISOString()
      item.observation_id = "observed-after-target"
    } else {
      item.sampled_at = new Date(
        Math.min(Date.now(), target - 1000),
      ).toISOString()
    }
    return route.fulfill({
      json: { item, checked_at: new Date().toISOString() },
    })
  })
  await page.goto("/")
  await page.getByRole("button", { name: "查看短时预测", exact: true }).click()
  const comparison = page.getByRole("region", {
    name: "预测到时对照",
    exact: true,
  })
  await comparison
    .getByRole("button", { name: "固定本次预测", exact: true })
    .click()
  await expect(comparison).toContainText("8.10 °C")
  await expect(page.getByTestId("forecast-outcome-pair")).toHaveCount(0)
  estimate = 9.0
  await page.getByRole("button", { name: "刷新预测", exact: true }).click()
  await expect(comparison).toContainText("8.10 °C")
  await expect(page.getByTestId("forecast-outcome-pair")).toContainText(
    "observed-after-target",
    { timeout: 30_000 },
  )
  await expect(page.getByTestId("forecast-outcome-pair")).toContainText(
    "0.20 °C",
  )
  await expect(comparison).toContainText("非整体性能评测")
})

test("Overview UI mock: server counts and selection remain explicitly scoped", async ({
  page,
}) => {
  const requested: URL[] = []
  await page.route(
    (url) => url.pathname === "/api/v1/devices/",
    (route) =>
      route.fulfill({
        json: {
          data: [device(first, "测点一"), device(second, "测点二", "offline")],
          count: 2,
        },
      }),
  )
  await page.route("**/api/v1/alerts/**", (route) =>
    route.fulfill({ json: { data: [], count: 7 } }),
  )
  await page.route("**/forecast-preview/status", (route) =>
    route.fulfill({ json: { enabled: false } }),
  )
  await page.route("**/observations/latest?**", (route) => {
    const url = new URL(route.request().url())
    requested.push(url)
    const id = url.searchParams.get("device_id")!
    return route.fulfill({
      json: {
        item: observation(id, id === first ? 4.5 : 7.25),
        checked_at: new Date().toISOString(),
      },
    })
  })
  await page.goto("/")
  await expect(
    page.getByRole("region", { name: "已注册设备", exact: true }),
  ).toContainText("2")
  await expect(
    page.getByRole("region", { name: "设备连接", exact: true }),
  ).toContainText("1 在线")
  await expect(
    page.getByRole("region", { name: "待处理告警", exact: true }),
  ).toContainText("7")
  await expect(page.getByTestId("overview-temperature")).toHaveText("4.50 °C")
  await page.getByLabel("温度测点", { exact: true }).selectOption(second)
  await expect(page.getByTestId("overview-temperature")).toHaveText("7.25 °C")
  expect(requested.at(-1)?.searchParams.get("device_id")).toBe(second)
  expect(requested.at(-1)?.searchParams.get("warehouse_id")).toBe(room)
  await expect(
    page.getByText("当前采用版本化阈值规则，未启用智能算法。", { exact: true }),
  ).toHaveCount(0)
})

test("Overview UI mock: failed refresh removes cached readings and all-clear counts", async ({
  page,
}) => {
  let failed = false
  await page.route(
    (url) => url.pathname === "/api/v1/devices/",
    (route) =>
      route.fulfill({ json: { data: [device(first, "测点一")], count: 1 } }),
  )
  await page.route("**/api/v1/alerts/**", (route) =>
    failed
      ? route.fulfill({ status: 503, json: { detail: "unavailable" } })
      : route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/forecast-preview/status", (route) =>
    route.fulfill({ json: { enabled: false } }),
  )
  await page.route("**/observations/latest?**", (route) =>
    failed
      ? route.fulfill({ status: 503, json: { detail: "unavailable" } })
      : route.fulfill({
          json: {
            item: observation(first, 4.5),
            checked_at: new Date().toISOString(),
          },
        }),
  )
  await page.goto("/")
  await expect(page.getByTestId("overview-temperature")).toHaveText("4.50 °C")
  failed = true
  await page.getByRole("button", { name: "刷新总览", exact: true }).click()
  await expect(
    page.getByText("采样读取失败，已隐藏上次数值。请检查服务后刷新。", {
      exact: true,
    }),
  ).toBeVisible()
  await expect(page.getByTestId("overview-temperature")).toHaveCount(0)
  const card = page.getByRole("region", { name: "待处理告警", exact: true })
  await expect(card).toContainText("—")
  await expect(card).toContainText("不代表没有告警")
})

test("Overview UI mock: a partial fleet is never labelled a complete online total", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname === "/api/v1/devices/",
    (route) =>
      route.fulfill({ json: { data: [device(first, "测点一")], count: 103 } }),
  )
  await page.route("**/api/v1/alerts/**", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/forecast-preview/status", (route) =>
    route.fulfill({ json: { enabled: false } }),
  )
  await page.route("**/observations/latest?**", (route) =>
    route.fulfill({
      json: { item: null, checked_at: new Date().toISOString() },
    }),
  )
  await page.goto("/")
  await expect(
    page.getByRole("region", { name: "已注册设备", exact: true }),
  ).toContainText("103")
  await expect(
    page.getByRole("region", { name: "设备连接", exact: true }),
  ).toContainText("当前列表范围")
  await expect(
    page.getByText("该测点尚无观测，不能据此判定环境正常。", { exact: true }),
  ).toBeVisible()
})

test("Overview UI mock: old, bad-quality and wrong-device observations cannot show current temperature", async ({
  page,
}) => {
  let mode = "stale"
  await page.route(
    (url) => url.pathname === "/api/v1/devices/",
    (route) =>
      route.fulfill({ json: { data: [device(first, "测点一")], count: 1 } }),
  )
  await page.route("**/api/v1/alerts/**", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/forecast-preview/status", (route) =>
    route.fulfill({ json: { enabled: false } }),
  )
  await page.route("**/observations/latest?**", (route) => {
    const item = observation(mode === "scope" ? second : first, 4.5)
    if (mode === "stale")
      item.received_at = new Date(Date.now() - 120_000).toISOString()
    if (mode === "late")
      item.sampled_at = new Date(Date.now() - 120_000).toISOString()
    if (mode === "quality") item.quality = ["missing_temperature"]
    return route.fulfill({
      json: { item, checked_at: new Date().toISOString() },
    })
  })
  await page.goto("/")
  await expect(page.getByTestId("overview-temperature")).toHaveText("—")
  await expect(
    page.getByText("观测已过期，等待新采样", { exact: true }),
  ).toBeVisible()
  mode = "late"
  const lateResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/observations/latest"),
  )
  await page.getByRole("button", { name: "刷新总览", exact: true }).click()
  await lateResponse
  await expect(page.getByTestId("overview-temperature")).toHaveText("—")
  mode = "quality"
  await page.getByRole("button", { name: "刷新总览", exact: true }).click()
  await expect(
    page.getByText("质量待核查，暂不提供当前温度", { exact: true }),
  ).toBeVisible()
  await expect(page.getByTestId("overview-temperature")).toHaveText("—")
  mode = "scope"
  await page.getByRole("button", { name: "刷新总览", exact: true }).click()
  await expect(
    page.getByText("观测归属与当前测点不一致，已隐藏数值。请刷新核查。", {
      exact: true,
    }),
  ).toBeVisible()
  await expect(page.getByTestId("overview-temperature")).toHaveCount(0)
})
