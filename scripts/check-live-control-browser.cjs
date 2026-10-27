// Opt-in ONLY. Existing Playwright/Chromium required; no package installation.
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

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
  const pageErrors = []
  const recordVideo = process.env.CONTROL_BROWSER_RECORD_VIDEO === "1"
  let recordingContext
  let recordingPage
  let proofPassed = false
  const holdFrame = async () => {
    if (recordVideo) await new Promise((resolve) => setTimeout(resolve, 3000))
  }
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      ...(recordVideo ? { recordVideo: { dir: path.join(results, "video"), size: { width: 1280, height: 900 } } } : {}),
    })
    recordingContext = context
    context.on("page", (target) => target.on("pageerror", (error) => {
      pageErrors.push({ name: error.name, message: error.message.slice(0, 1500) })
    }))
    // Block outside origins; same-origin requests are NEVER fulfilled/mocked.
    await context.route("**/*", (route) => {
      const target = new URL(route.request().url())
      return target.origin === url.origin ? route.continue() : route.abort()
    })
    const page = await context.newPage()
    recordingPage = page
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
    await holdFrame()
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
    await holdFrame()
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
    // A-owned shared navigation proof. Synthetic candidate enters through real
    // authenticated B HTTP, not direct SQL or an intercepted API response.
    // B's explicitly handed-off lifecycle repair is exercised below. This is
    // synthetic input, not a C-triggered anomaly or a real notification proof.
    const warehouses = await context.request.get(`${origin}/api/v1/warehouses/`, { headers })
    assert.equal(warehouses.status(), 200)
    const warehouseRows = await warehouses.json()
    assert(Array.isArray(warehouseRows) && warehouseRows.length === 1)
    const warehouseId = warehouseRows[0].id
    const batch = await context.request.post(`${origin}/api/v1/warehouses/${warehouseId}/batches`, {
      headers, data: { code: "alerts-browser-synthetic", product: "apple" },
    })
    assert.equal(batch.status(), 201)
    const sourceId = "alerts-browser-synthetic-anomaly"
    const candidateBody = {
        schema_version: "1.0", source_event_id: sourceId,
        warehouse_id: warehouseId, batch_id: (await batch.json()).id,
        observation_ids: ["synthetic-browser-observation"], metric_code: "temperature",
        sampled_at: new Date().toISOString(), value: 11, unit: "°C",
        kind: "temperature_high", severity: "warning", rule_version: "v1",
        algorithm_version: "synthetic-browser-proof/1",
        evidence: { comparison: ">", threshold: 8, synthetic_input: true },
    }
    const candidate = await context.request.post(`${origin}/api/v1/candidates`, {
      headers, data: candidateBody,
    })
    assert.equal(candidate.status(), 201)
    const eventId = (await candidate.json()).event_id
    for (const targetPage of [page, readerPage]) {
      await targetPage.keyboard.press("Escape")
      await expect(targetPage.getByRole("dialog")).toHaveCount(0)
      await targetPage.getByRole("link", { name: "告警处置", exact: true }).click()
      await expect(targetPage).toHaveURL(`${origin}/alerts`)
      const row = targetPage.getByRole("row").filter({ hasText: sourceId })
      await expect(row).toContainText("温度超上限")
      await row.getByRole("button", { name: "查看 / 处置", exact: true }).click()
      await expect(targetPage.getByRole("dialog")).toContainText(eventId)
    }
    await expect(page.getByRole("dialog").getByRole("button", { name: "确认告警", exact: true })).toBeEnabled()
    await expect(readerPage.getByRole("dialog")).toContainText("当前为只读访问")
    await expect(readerPage.getByRole("dialog").getByRole("button", { name: "确认告警", exact: true })).toHaveCount(0)
    const deniedAck = await readerContext.request.post(`${origin}/api/v1/alerts/${eventId}/acknowledge`, {
      headers: { Authorization: `Bearer ${readerToken}` },
    })
    assert.equal(deniedAck.status(), 403)
    const unchanged = await context.request.get(`${origin}/api/v1/alerts/${eventId}/status`, { headers })
    assert.equal(unchanged.status(), 200)
    const lifecycle = await unchanged.json()
    assert.equal(lifecycle.status, "open")
    assert.deepEqual(lifecycle.transitions, [])
    await readerPage.screenshot({ path: path.join(results, "alerts-reader-real-readonly.png"), fullPage: true })
    const ackPath = `/api/v1/alerts/${eventId}/acknowledge`
    const ackResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === ackPath && response.request().method() === "POST")
    await page.getByRole("dialog").getByRole("button", { name: "确认告警", exact: true }).click()
    const acknowledged = await ackResponse
    assert.equal(acknowledged.status(), 200)
    const ackBody = await acknowledged.json()
    assert.equal(ackBody.status, "acknowledged")
    assert.equal(ackBody.transition_created, true)
    assert.equal(ackBody.transitions.length, 1)
    await expect(page.getByRole("dialog").getByRole("button", { name: "确认告警", exact: true })).toBeDisabled()
    await expect(page.getByRole("dialog").getByRole("region", { name: "状态迁移记录", exact: true }))
      .toContainText("待处理 → 已确认")
    await page.screenshot({ path: path.join(results, "alerts-admin-real-ack.png"), fullPage: true })
    await holdFrame()
    const ackReplay = await context.request.post(`${origin}${ackPath}`, { headers })
    assert.equal(ackReplay.status(), 200)
    assert.equal((await ackReplay.json()).transition_created, false)
    const recovery = await context.request.post(`${origin}/api/v1/candidates`, {
      headers, data: {
        ...candidateBody,
        source_event_id: "alerts-browser-synthetic-recovery",
        related_source_event_id: sourceId,
        kind: "temperature_recovered", value: 7,
        sampled_at: new Date().toISOString(),
      },
    })
    assert.equal(recovery.status(), 201)
    const lateAck = await context.request.post(`${origin}${ackPath}`, { headers })
    assert.equal(lateAck.status(), 409)
    const resolvedResponse = await context.request.get(`${origin}/api/v1/alerts/${eventId}/status`, { headers })
    assert.equal(resolvedResponse.status(), 200)
    const resolved = await resolvedResponse.json()
    assert.equal(resolved.status, "resolved")
    assert.equal(resolved.transitions.length, 2)
    assert.deepEqual(resolved.transitions.map((item) => [item.from_status, item.to_status]), [
      ["open", "acknowledged"], ["acknowledged", "resolved"],
    ])
    await expect(page.getByRole("dialog").getByRole("region", { name: "状态迁移记录", exact: true }))
      .toContainText("已确认 → 已恢复", { timeout: 15000 })
    await page.screenshot({ path: path.join(results, "alerts-admin-real-resolved.png"), fullPage: true })
    await holdFrame()
    // Same running combination: continuous actual MQTT/A inbox/C persistence
    // feeds the unmodified C page and actual authenticated snapshot/CSV routes.
    const sensorId = "11111111-1111-4111-8111-111111111111"
    await page.keyboard.press("Escape")
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await page.getByRole("link", { name: "遥测历史", exact: true }).click()
    await expect(page).toHaveURL(`${origin}/telemetry`)
    await page.getByLabel("设备 ID", { exact: true }).fill(sensorId)
    const snapshotResponse = page.waitForResponse((response) => {
      const target = new URL(response.url())
      return target.pathname === "/api/v1/telemetry/observations/" && target.searchParams.get("device_id") === sensorId
    })
    await page.getByRole("button", { name: "按筛选新建快照", exact: true }).click()
    const actualSnapshotResponse = await snapshotResponse
    assert.equal(actualSnapshotResponse.status(), 200)
    const snapshot = await actualSnapshotResponse.json()
    assert(snapshot.total >= 3 && snapshot.items.length >= 3)
    assert(snapshot.items.every((item) => item.device_id === sensorId && item.warehouse_id === warehouseId))
    await expect(page.getByRole("row").filter({ hasText: sensorId }).first()).toBeVisible()
    const detailResponseWait = page.waitForResponse((response) =>
      new URL(response.url()).pathname === `/api/v1/telemetry/observations/${snapshot.items[0].observation_id}`)
    await page.getByRole("row").filter({ hasText: sensorId }).first().getByRole("button", { name: "查看", exact: true }).click()
    const rawResponse = await detailResponseWait
    assert.equal(rawResponse.status(), 200)
    const raw = await rawResponse.json()
    const rawPayload = JSON.parse(raw.raw_text)
    assert.equal(rawPayload.device_id, sensorId)
    assert(rawPayload.metrics.some((item) => item.key === "temperature" && typeof item.value === "number"))
    assert(raw.receptions.length >= 1 && raw.quality.includes("ok"))
    await expect(page.locator("pre")).toHaveText(raw.raw_text)
    const exportResponseWait = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/v1/telemetry/observations/export.csv")
    const downloadWait = page.waitForEvent("download")
    await page.getByRole("button", { name: "导出本次快照 CSV", exact: true }).click()
    const exportResponse = await exportResponseWait
    assert.equal(exportResponse.status(), 200)
    const exportUrl = new URL(exportResponse.url())
    assert.equal(exportUrl.searchParams.get("snapshot_id"), snapshot.snapshot_id)
    assert.equal(exportUrl.searchParams.get("device_id"), sensorId)
    const download = await downloadWait
    const csvPath = path.join(results, "telemetry-real-snapshot.csv")
    await download.saveAs(csvPath)
    const csvText = fs.readFileSync(csvPath, "utf8").replace(/^\uFEFF/, "")
    const csvLines = csvText.trimEnd().split(/\r?\n/)
    assert.equal(csvLines.length - 1, snapshot.total)
    const snapshotIds = snapshot.items.map((item) => item.observation_id)
    let cursor = snapshot.next_cursor
    const visited = new Set()
    while (cursor) {
      assert(!visited.has(cursor) && visited.size < 32, "snapshot pagination must be bounded")
      visited.add(cursor)
      const next = await context.request.get(`${origin}/api/v1/telemetry/observations/`, {
        headers, params: { device_id: sensorId, snapshot_id: snapshot.snapshot_id, cursor },
      })
      assert.equal(next.status(), 200)
      const body = await next.json()
      assert.equal(body.snapshot_id, snapshot.snapshot_id)
      assert.equal(body.total, snapshot.total)
      snapshotIds.push(...body.items.map((item) => item.observation_id))
      cursor = body.next_cursor
    }
    const csvIds = csvLines.slice(1).map((line) => line.split(",")[0].replace(/^"|"$/g, ""))
    assert.equal(new Set(snapshotIds).size, snapshot.total)
    assert.deepEqual(csvIds.sort(), snapshotIds.sort())
    assert(csvLines.slice(1).every((line) => line.includes(sensorId) && line.includes(warehouseId)))
    // New live samples must NOT change membership in the earlier snapshot.
    const sameSnapshot = await context.request.get(`${origin}/api/v1/telemetry/observations/`, {
      headers, params: { device_id: sensorId, snapshot_id: snapshot.snapshot_id },
    })
    assert.equal(sameSnapshot.status(), 200)
    assert.equal((await sameSnapshot.json()).total, snapshot.total)
    const anonymous = await browser.newContext()
    const deniedHistory = await anonymous.request.get(`${origin}/api/v1/telemetry/observations/`, {
      params: { device_id: sensorId },
    })
    assert.equal(deniedHistory.status(), 401)
    await anonymous.close()
    await page.screenshot({ path: path.join(results, "telemetry-real-history-raw-csv.png"), fullPage: true })
    await holdFrame()
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
      alerts_navigation_list_detail_and_reader_post_403: true,
      alerts_synthetic_source: sourceId,
      alerts_event_id: eventId,
      alerts_denied_write_left_zero_transitions: true,
      alerts_admin_ui_ack_real_http: true,
      alerts_same_id_ack_replay_created_no_second_transition: true,
      alerts_synthetic_recovery_then_late_ack_409: true,
      alerts_final_append_only_transitions: resolved.transitions.length,
      telemetry_live_mqtt_a_c_page_and_raw: true,
      telemetry_snapshot_id: snapshot.snapshot_id,
      telemetry_snapshot_total: snapshot.total,
      telemetry_same_filtered_snapshot_csv_rows: csvLines.length - 1,
      telemetry_csv_all_ids_equal_all_snapshot_pages: true,
      telemetry_snapshot_membership_stable: true,
      telemetry_anonymous_http_401: true,
      no_mocked_api: true,
      not_tested: ["concurrency (separate PG suite)", "C-triggered B anomaly/recovery", "two real notifications", "formal A/C migration chain", "full E1"],
    }, null, 2), { flag: "wx" })
    proofPassed = true
    console.log("PASS real browser login/control/receipt/reload/ordinary-user refusal; no API mocks")
  } catch (error) {
    // All contexts here target the disposable origin and synthetic accounts.
    // Preserve the actual rendered failure; never replace a failing check.
    const pages = browser.contexts().flatMap((context) => context.pages())
    const views = []
    for (let index = 0; index < pages.length; index += 1) {
      const target = pages[index]
      if (!target.url().startsWith(url.origin + "/")) continue
      views.push({ path: new URL(target.url()).pathname,
        text: (await target.locator("body").innerText()).slice(0, 6000) })
      await target.screenshot({ path: path.join(results, `failure-${index}.png`), fullPage: true })
    }
    fs.writeFileSync(path.join(results, "browser-failure.json"),
      JSON.stringify({ pageErrors, views }, null, 2), { flag: "wx" })
    throw error
  } finally {
    try {
      if (recordVideo && recordingContext) {
      await recordingContext.close()
      if (recordingPage && proofPassed) {
        const videoPath = await recordingPage.video().path()
        const bytes = fs.readFileSync(videoPath)
        fs.writeFileSync(path.join(results, "video-recording.json"), JSON.stringify({
          phase: "actual_browser_recording_not_final_m05",
          file: path.relative(results, videoPath),
          bytes: bytes.length,
          sha256: require("node:crypto").createHash("sha256").update(bytes).digest("hex"),
          mocked_api: false,
          synthetic_alert_input: true,
          real_notification_receipts: false,
          complete_e1: false,
          final_video_acceptance: false,
          note: "Actual isolated browser actions with disposable accounts. Alert recovery input is synthetic. See browser.json and server.json for the tested scope.",
        }, null, 2), { flag: "wx" })
      }
      }
    } finally {
      await browser.close()
    }
  }
}
run().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
