import { expect, test } from "@playwright/test"
import type { AlertRow } from "../src/features/alerts/api"

test("correction list shows its own source and linked original", async ({ page }) => {
  const row: AlertRow = {
    alert_event_id: "11111111-1111-4111-8111-111111111111",
    source_event_id: "correction-source-1",
    related_source_event_id: "original-temperature-source-1",
    kind: "data_quality",
    severity: "info",
    warehouse_id: "22222222-2222-4222-8222-222222222222",
    batch_id: "33333333-3333-4333-8333-333333333333",
    device_id: null,
    metric_code: "temperature",
    sampled_at: "2026-10-25T08:00:00Z",
    value: null,
    unit: "°C",
    status: "open",
    created_at: "2026-10-25T08:00:01Z",
  }
  await page.route("**/api/v1/alerts/**", async (route) => {
    await route.fulfill({ json: { data: [row], count: 1 } })
  })
  await page.goto("/alerts")
  const tableRow = page.getByRole("row").filter({ hasText: row.source_event_id })
  await expect(tableRow.getByText(row.source_event_id, { exact: true })).toBeVisible()
  await expect(
    tableRow.getByText(`关联原异常：${row.related_source_event_id}`, { exact: true }),
  ).toBeVisible()
})
