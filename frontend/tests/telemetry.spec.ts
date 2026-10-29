import { expect, test } from "@playwright/test"

const deviceId = "11111111-1111-4111-8111-111111111111"
const warehouseId = "22222222-2222-4222-8222-222222222222"
const snapshotId = "33333333-3333-4333-8333-333333333333"

function dataFilters(url: URL): string[] {
  const params = new URLSearchParams(url.search)
  params.delete("snapshot_id")
  params.delete("cursor")
  params.delete("limit")
  params.delete("export_schema")
  return [...params.entries()].map(([key, value]) => `${key}=${value}`).sort()
}

test("Telemetry UI mock: CSV uses the filtered list's fixed snapshot", async ({
  page,
}) => {
  const lists: URL[] = []
  const exports: URL[] = []
  await page.route("**/api/v1/telemetry/observations/**", async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith("/latest")) {
      await route.fulfill({
        json: { item: null, checked_at: "2026-10-24T00:00:00Z" },
      })
    } else if (url.pathname.endsWith("/export.csv")) {
      exports.push(url)
      await route.fulfill({
        status: 200,
        contentType: "text/csv; charset=utf-8",
        body: "observation_id,temperature_c\r\n",
      })
    } else if (url.pathname === "/api/v1/telemetry/observations/") {
      lists.push(url)
      await route.fulfill({
        json: {
          snapshot_id: snapshotId,
          items: [],
          total: 0,
          next_cursor: null,
        },
      })
    } else {
      await route.abort()
    }
  })

  await page.goto("/telemetry")
  await page.getByLabel("设备 ID").fill(deviceId)
  await page.getByLabel("冷库 ID").fill(warehouseId)
  await page.getByLabel("采样起始 UTC").fill("2026-10-24T09:00")
  await page.getByLabel("质量标记").fill("missing_temperature")
  const filteredResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return (
      url.pathname === "/api/v1/telemetry/observations/" &&
      url.searchParams.get("device_id") === deviceId
    )
  })
  await page.getByRole("button", { name: "按筛选新建快照" }).click()
  await filteredResponse
  await expect(
    page.getByText("该筛选没有观测；空结果不代表环境正常。"),
  ).toBeVisible()
  await expect(page.getByText("暂无保存观测；当前风险未知。")).toBeVisible()

  const downloadPromise = page.waitForEvent("download")
  await page
    .getByRole("button", { name: "导出本次快照 CSV（含批次规则）" })
    .click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe("observations.csv")
  const filtered = lists.at(-1)
  const exported = exports[0]
  if (!filtered || !exported) {
    throw new Error("filtered list or CSV was not requested")
  }
  expect(filtered.searchParams.get("device_id")).toBe(deviceId)
  expect(filtered.searchParams.get("warehouse_id")).toBe(warehouseId)
  expect(filtered.searchParams.get("sampled_from")).toBe("2026-10-24T09:00Z")
  expect(exported.searchParams.get("snapshot_id")).toBe(snapshotId)
  expect(exported.searchParams.get("export_schema")).toBe("2")
  expect(dataFilters(exported)).toEqual(dataFilters(filtered))
})

test("Telemetry UI mock: high, missing and rejected receptions remain visible", async ({
  page,
}) => {
  const highId = "44444444-4444-4444-8444-444444444444"
  const missingId = "55555555-5555-4555-8555-555555555555"
  const base = {
    device_id: deviceId,
    warehouse_id: warehouseId,
    received_at: "2026-10-24T00:00:01Z",
    humidity_percent: 75,
  }
  await page.route("**/api/v1/telemetry/observations/**", async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith("/latest")) {
      await route.fulfill({
        json: { item: null, checked_at: "2026-10-24T00:00:02Z" },
      })
    } else if (url.pathname.endsWith(`/${highId}`)) {
      await route.fulfill({
        json: {
          observation_id: highId,
          raw_text: '{"temperature":13}',
          normalized: {},
          quality: ["ok"],
          risk: { state: "candidate" },
          reception_reuse_conflict_count: 1,
          receptions: [
            {
              reception_id: "66666666-6666-4666-8666-666666666666",
              received_at: "2026-10-24T00:00:03Z",
              outcome: "conflict",
              reason: "conflicting_message",
            },
          ],
        },
      })
    } else if (url.pathname === "/api/v1/telemetry/observations/") {
      await route.fulfill({
        json: {
          snapshot_id: snapshotId,
          total: 2,
          next_cursor: null,
          items: [
            {
              ...base,
              observation_id: highId,
              sampled_at: "2026-10-24T00:00:00Z",
              temperature_c: 13,
              quality: ["ok"],
              risk_state: "candidate",
              rule_version: null,
              batch_rule_refs: [
                {
                  batch_id: "77777777-7777-4777-8777-777777777777",
                  rule_version: "temp-v1",
                  rule_snapshot_id: "88888888-8888-4888-8888-888888888888",
                  state: "candidate",
                },
                {
                  batch_id: "99999999-9999-4999-8999-999999999999",
                  rule_version: "temp-v2",
                  rule_snapshot_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                  state: "normal",
                },
              ],
            },
            {
              ...base,
              observation_id: missingId,
              sampled_at: null,
              temperature_c: null,
              quality: ["missing_temperature"],
              risk_state: "unknown",
              rule_version: null,
            },
          ],
        },
      })
    } else {
      await route.abort()
    }
  })

  await page.goto("/telemetry")
  await expect(page.getByText("观测列表（固定快照共 2 条）")).toBeVisible()
  const rows = page.getByRole("row")
  await expect(rows.nth(1)).toContainText("13")
  await expect(rows.nth(1)).toContainText("candidate")
  await rows.nth(1).getByText("多批次规则（2）").click()
  await expect(rows.nth(1)).toContainText("temp-v2")
  await expect(rows.nth(2)).toContainText("缺失 / 未知")
  await expect(rows.nth(2)).toContainText("missing_temperature / unknown")
  await expect(rows.nth(2)).toContainText("未评估；规则证据需核查")

  await rows.nth(1).getByRole("button", { name: "查看" }).click()
  await expect(page.getByText('{"temperature":13}')).toBeVisible()
  await expect(page.getByText(/接收 ID 异文复用/)).toBeVisible()
  await expect(page.getByText(/存在被拒绝的接收记录/)).toBeVisible()
})
