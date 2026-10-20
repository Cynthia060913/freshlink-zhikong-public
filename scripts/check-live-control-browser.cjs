// Opt-in ONLY. Existing Playwright/Chromium required; no package installation.
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

function parseCsv(text) {
  const rows = []
  let row = [], cell = "", quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (char === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i += 1 }
      else quoted = !quoted
    } else if (char === "," && !quoted) { row.push(cell); cell = "" }
    else if (char === "\n" && !quoted) {
      row.push(cell.endsWith("\r") ? cell.slice(0, -1) : cell)
      rows.push(row); row = []; cell = ""
    } else cell += char
  }
  assert.equal(quoted, false)
  assert.equal(cell, "")
  assert.equal(row.length, 0)
  return rows
}

function checkCoolingHistory(items, ackAt, checkpointId) {
  const ack = Date.parse(ackAt)
  assert(Number.isFinite(ack))
  const before = items.filter((item) => Date.parse(item.received_at) <= ack)
    .sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at))
  const after = items.filter((item) => Date.parse(item.received_at) > ack)
  assert(before.length && after.length, "C history must span actual ACK")
  assert(after.some((item) => item.observation_id === checkpointId), "verified post-ACK sample must belong to snapshot")
  const baseline = before[before.length - 1].temperature_c
  const minimum = Math.min(...after.map((item) => item.temperature_c))
  assert(minimum < baseline - 0.05, "C history must contain cooling after the actual command")
  return { baseline, minimum }
}

async function run() {
  const [origin, results, moduleRoot, executablePath] = process.argv.slice(2)
  const url = new URL(origin)
  assert.equal(url.hostname, "127.0.0.1")
  assert.equal(url.protocol, "http:")
  assert.match(url.port, /^\d+$/)
  assert(fs.existsSync(path.join(results, "ready.json")))
  assert(!fs.existsSync(path.join(results, "browser.json")))
  const { chromium, expect } = require(path.resolve(moduleRoot, "playwright/test"))
  const browser = await chromium.launch({ executablePath, headless: true })
  const actuatorId = "66666666-6666-4666-8666-666666666666"
  const commandPath = `/api/v1/devices/${actuatorId}/commands`
  let submitted
  let postCount = 0
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    // Block outside origins; same-origin requests are NEVER fulfilled/mocked.
    await context.route("**/*", (route) => {
      const target = new URL(route.request().url())
      return target.origin === url.origin ? route.continue() : route.abort()
    })
    const page = await context.newPage()
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === commandPath && request.method() === "POST") {
        postCount += 1
        submitted = request.postDataJSON()
      }
    })
    async function login(targetPage, email, password) {
      await targetPage.goto(`${origin}/login`)
      await targetPage.getByTestId("email-input").fill(email)
      await targetPage.getByTestId("password-input").fill(password)
      await targetPage.getByRole("button", { name: "Log In", exact: true }).click()
      await expect(targetPage).not.toHaveURL(/\/login$/)
      await targetPage.goto(`${origin}/devices`)
      await targetPage.getByRole("row").filter({ hasText: "browser-cooling_actuator" })
        .getByRole("button", { name: "查看详情" }).click()
      return targetPage.getByRole("region", { name: "远程制冷控制", exact: true })
    }
    const panel = await login(page, "admin@example.com", "a01-disposable-admin-only")
    await expect(panel.getByRole("button", { name: "准备下发", exact: true })).toBeEnabled()
    await panel.getByRole("button", { name: "准备下发", exact: true }).click()
    assert.equal(postCount, 0)
    await panel.getByRole("button", { name: "取消下发", exact: true }).click()
    assert.equal(postCount, 0)
    await panel.getByRole("button", { name: "准备下发", exact: true }).click()
    const accepted = page.waitForResponse((response) =>
      new URL(response.url()).pathname === commandPath && response.request().method() === "POST")
    await panel.getByRole("button", { name: "确认下发开启制冷", exact: true }).click()
    assert.equal((await accepted).status(), 201)
    await expect(panel.getByRole("status").filter({ hasText: "设备回执：执行成功" }))
      .toBeVisible({ timeout: 20000 })
    assert.equal(postCount, 1)
    assert.equal(submitted.enabled, true)
    const token = await page.evaluate(() => localStorage.getItem("access_token"))
    assert(token)
    const headers = { Authorization: `Bearer ${token}` }
    const detailResponse = await context.request.get(`${origin}${commandPath}/${submitted.command_id}`, { headers })
    assert.equal(detailResponse.status(), 200)
    const detail = await detailResponse.json()
    assert.equal(detail.status, "acknowledged")
    assert.equal(detail.receipts.length, 1)
    assert.equal(detail.receipts[0].outcome, "accepted")
    assert.equal(detail.receipts[0].result, "executed")
    assert(detail.ack_received_at)
    await panel.getByRole("status").filter({ hasText: "设备回执：执行成功" }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(results, "admin-real-ack.png"), fullPage: true })
    // Real same-ID HTTP replay must not create another command/execution.
    const replay = await context.request.post(`${origin}${commandPath}`, { headers, data: submitted })
    assert.equal(replay.status(), 201)
    assert.equal((await replay.json()).command_id, submitted.command_id)
    await page.reload()
    await page.getByRole("row").filter({ hasText: "browser-cooling_actuator" })
      .getByRole("button", { name: "查看详情" }).click()
    const reopened = page.getByRole("region", { name: "远程制冷控制", exact: true })
    await reopened.getByRole("button", { name: "查询此命令", exact: true }).click()
    await expect(reopened.getByRole("status").filter({ hasText: "设备回执：执行成功" })).toBeVisible()
    const signup = await context.request.post(`${origin}/api/v1/users/signup`, {
      data: { email: "reader@example.com", password: "browser-reader-test-only" },
    })
    assert.equal(signup.status(), 200)
    const readerContext = await browser.newContext()
    await readerContext.route("**/*", (route) =>
      new URL(route.request().url()).origin === url.origin ? route.continue() : route.abort())
    const readerPage = await readerContext.newPage()
    const readerPanel = await login(readerPage, "reader@example.com", "browser-reader-test-only")
    await expect(readerPanel).toContainText("只有管理员可下发控制")
    await expect(readerPanel.getByRole("button", { name: "准备下发", exact: true })).toHaveCount(0)
    await readerPanel.getByRole("button", { name: "查询此命令", exact: true }).click()
    await expect(readerPanel.getByRole("status").filter({ hasText: "设备回执：执行成功" })).toBeVisible()
    const readerToken = await readerPage.evaluate(() => localStorage.getItem("access_token"))
    const refused = await readerContext.request.post(`${origin}${commandPath}`, {
      headers: { Authorization: `Bearer ${readerToken}` },
      data: { ...submitted, command_id: "77777777-7777-4777-8777-777777777777", enabled: false },
    })
    assert.equal(refused.status(), 403)
    const history = await context.request.get(`${origin}${commandPath}`, { headers })
    assert.equal(history.status(), 200)
    assert.equal((await history.json()).count, 1)
    await readerPanel.getByRole("status").filter({ hasText: "设备回执：执行成功" }).scrollIntoViewIfNeeded()
    await readerPage.screenshot({ path: path.join(results, "reader-real-readonly.png"), fullPage: true })
    // Real C page over the actual continuous MQTT -> A queue -> C PG path.
    const ready = JSON.parse(fs.readFileSync(path.join(results, "ready.json"), "utf8"))
    fs.writeFileSync(path.join(results, "control-checkpoint.json"),
      JSON.stringify({command_id: submitted.command_id}), {flag: "wx"})
    await expect.poll(() => fs.existsSync(path.join(results, "telemetry-after-control.json")),
      {timeout: 20000, intervals: [100, 200, 500]}).toBe(true)
    const afterControl = JSON.parse(fs.readFileSync(path.join(results, "telemetry-after-control.json"), "utf8"))
    assert.equal(afterControl.command_id, submitted.command_id)
    await page.goto(`${origin}/telemetry`)
    await expect(page.getByRole("heading", { name: "遥测历史", exact: true })).toBeVisible()
    await page.getByLabel("设备 ID", { exact: true }).fill(ready.sensor_id)
    await page.getByLabel("冷库 ID", { exact: true }).fill(ready.warehouse_id)
    const filteredResponse = page.waitForResponse((response) => {
      const target = new URL(response.url())
      return target.pathname === "/api/v1/telemetry/observations/"
        && target.searchParams.get("device_id") === ready.sensor_id
        && !target.searchParams.has("snapshot_id")
    })
    await page.getByRole("button", { name: "按筛选新建快照", exact: true }).click()
    const filtered = await filteredResponse
    assert.equal(filtered.status(), 200)
    const first = await filtered.json()
    fs.writeFileSync(path.join(results, "telemetry-query-summary.json"), JSON.stringify({
      total: first.total, items: first.items?.length,
      has_next_cursor: Boolean(first.next_cursor),
      query: new URL(filtered.url()).search,
    }, null, 2), {flag: "wx"})
    assert(first.total >= 25 && first.next_cursor)
    await expect(page.locator("tbody tr")).toHaveCount(20)
    await page.locator("tbody tr").first().getByRole("button", { name: "查看", exact: true }).click()
    await expect(page.getByRole("heading", { name: "观测原始证据", exact: true })).toBeVisible()
    await expect(page.locator("pre")).toContainText(ready.sensor_id)
    const allItems = [...first.items]
    let current = first
    for (let pages = 0; current.next_cursor; pages += 1) {
      assert(pages < 20, "bounded snapshot pagination")
      const nextResponse = page.waitForResponse((response) => {
        const target = new URL(response.url())
        return target.pathname === "/api/v1/telemetry/observations/"
          && target.searchParams.get("cursor") === current.next_cursor
      })
      await page.getByRole("button", { name: "下一页", exact: true }).click()
      const next = await nextResponse
      assert.equal(next.status(), 200)
      current = await next.json()
      assert.equal(current.snapshot_id, first.snapshot_id)
      assert.equal(current.total, first.total)
      await expect(page.locator("tbody tr").first()).toContainText(current.items[0].sampled_at)
      allItems.push(...current.items)
    }
    assert.equal(allItems.length, first.total)
    assert.equal(new Set(allItems.map((item) => item.observation_id)).size, first.total)
    assert(allItems.every((item) => item.device_id === ready.sensor_id
      && item.warehouse_id === ready.warehouse_id && item.risk_state === "unknown"))
    const temperatures = allItems.map((item) => item.temperature_c)
    assert(temperatures.every(Number.isFinite))
    const cooling = checkCoolingHistory(allItems, detail.ack_received_at, afterControl.observation_id)
    const downloadEvent = page.waitForEvent("download")
    await page.getByRole("button", { name: "导出本次快照 CSV", exact: true }).click()
    const download = await downloadEvent
    assert.equal(await download.failure(), null)
    const csvPath = path.join(results, "observations.csv")
    assert(!fs.existsSync(csvPath))
    await download.saveAs(csvPath)
    const csv = fs.readFileSync(csvPath, "utf8")
    assert(csv.startsWith("\uFEFF"))
    const csvRows = parseCsv(csv.slice(1))
    const idColumn = csvRows[0].indexOf("observation_id")
    assert(idColumn >= 0)
    assert.deepEqual(csvRows.slice(1).map((row) => row[idColumn]),
      allItems.map((item) => item.observation_id))
    const ownedSnapshotUrl = new URL(`${origin}/api/v1/telemetry/observations/export.csv`)
    ownedSnapshotUrl.search = new URLSearchParams({device_id: ready.sensor_id,
      warehouse_id: ready.warehouse_id, snapshot_id: first.snapshot_id}).toString()
    const anonymousCsv = await context.request.get(ownedSnapshotUrl.href)
    assert.equal(anonymousCsv.status(), 401)
    const readerCsv = await readerContext.request.get(ownedSnapshotUrl.href, {
      headers: {Authorization: `Bearer ${readerToken}`},
    })
    assert.equal(readerCsv.status(), 422)
    await page.screenshot({path: path.join(results, "telemetry-real-history.png"), fullPage: true})
    fs.writeFileSync(path.join(results, "browser.json"), JSON.stringify({
      phase: "real_browser_http_mqtt_control_passed",
      command_id: submitted.command_id,
      browser_version: browser.version(),
      playwright_version: require(path.resolve(moduleRoot, "playwright/package.json")).version,
      prepare_cancel_post_count: 0,
      browser_control_post_count: postCount,
      real_same_id_replay: true,
      reload_kept_real_receipt: true,
      reader_readonly_and_post_403: true,
      no_mocked_api: true,
      telemetry_history_csv_passed: true,
      telemetry_snapshot_total: first.total,
      telemetry_snapshot_id: first.snapshot_id,
      c_temperature_before_ack: cooling.baseline,
      c_temperature_after_ack_min: cooling.minimum,
      csv_same_snapshot_all_pages: true,
      c_raw_detail_visible: true,
      anonymous_csv_401_and_other_actor_422: true,
      not_tested: ["formal A/C migration", "automatic evaluation/recovery", "two real notifications", "full E1"],
    }, null, 2), { flag: "wx" })
    console.log("PASS real browser login/control/receipt/reload/ordinary-user refusal; no API mocks")
  } finally {
    await browser.close()
  }
}
if (process.argv[2] === "--csv-self-test") {
  assert.deepEqual(parseCsv('"a","b"\r\n"x","y"\r\n'), [["a", "b"], ["x", "y"]])
  assert.deepEqual(parseCsv('"a,b","quote""inside"\r\n'), [["a,b", 'quote"inside']])
  assert.deepEqual(parseCsv('"line\nbreak",""\r\n'), [["line\nbreak", ""]])
  assert.throws(() => parseCsv('"unfinished\n'))
  const rows = [
    {observation_id:"start",received_at:"2026-10-20T00:00:00Z",temperature_c:10},
    {observation_id:"warm",received_at:"2026-10-20T00:00:10Z",temperature_c:10.3},
    {observation_id:"cool",received_at:"2026-10-20T00:00:12Z",temperature_c:10.1},
  ]
  assert.deepEqual(checkCoolingHistory(rows,"2026-10-20T00:00:11Z","cool"), {baseline:10.3,minimum:10.1})
  assert.throws(() => checkCoolingHistory(rows.slice(0,2),"2026-10-20T00:00:11Z","cool"))
  console.log("PASS 4 CSV parser and 2 cooling-baseline regression cases")
} else {
  run().catch((error) => {
    console.error(error.message)
    process.exitCode = 1
  })
}
