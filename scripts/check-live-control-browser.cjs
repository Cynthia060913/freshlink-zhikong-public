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
      not_tested: ["C history/CSV", "two real notifications", "full E1"],
    }, null, 2), { flag: "wx" })
    console.log("PASS real browser login/control/receipt/reload/ordinary-user refusal; no API mocks")
  } finally {
    await browser.close()
  }
}
run().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
