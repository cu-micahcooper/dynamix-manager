const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const baseURL = process.argv[2];
  if (!baseURL) throw new Error('Usage: node ticket-write-browser.cjs https://127.0.0.1:PORT');
  const screenshotDir = process.argv[3] || process.env.TICKET_WRITE_SCREENSHOT_DIR;
  if (screenshotDir) fs.mkdirSync(screenshotDir, { recursive: true });

  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    const browserErrors = [];
    const recordBrowserErrors = (target) => {
      target.on('console', (message) => {
        if (message.type() === 'error') browserErrors.push(message.text());
      });
      target.on('pageerror', (error) => browserErrors.push(error.message));
    };
    recordBrowserErrors(page);

    const reset = await context.request.post(`${baseURL}/test/reset`);
    assert.equal(reset.ok(), true);
    const scenarios = (await reset.json()).review_urls;

    await page.goto(scenarios.comment, { waitUntil: 'networkidle' });
    assert.equal(page.url().includes('#'), false, 'fragment must be removed before open POST');
    assert.equal(await page.getByText('Not saved', { exact: true }).isVisible(), true);
    assert.equal(await page.getByText('Public', { exact: true }).isVisible(), true);
    assert.equal(await page.getByText('requester@example.invalid, observer@example.invalid').isVisible(), true);
    if (screenshotDir) {
      await page.screenshot({ path: path.join(screenshotDir, 'comment-desktop.png'), fullPage: true });
    }
    const browserCookie = (await context.cookies(baseURL)).find(
      (cookie) => cookie.name === '__Host-tdx-write'
    );
    assert.equal(Boolean(browserCookie && browserCookie.secure && browserCookie.httpOnly
      && browserCookie.sameSite === 'Strict'), true,
    'review open must establish a Strict HttpOnly cookie');
    let status = await (await context.request.get(`${baseURL}/test/status`)).json();
    assert.equal(status.apply_count['1201'], 0, 'opening a review must never apply');
    const commentRequests = status.requests.filter((request) => request.path.startsWith('/writes/'));
    assert.equal(commentRequests[0].path, '/writes/review');
    assert.equal(commentRequests[0].query, '');
    assert.equal(commentRequests[1].path, '/writes/open');
    assert.equal(commentRequests[1].origin, baseURL);
    assert.equal(Object.values(scenarios).some((url) =>
      commentRequests.some((request) => request.path.includes(url.split('#')[1]) || request.query.includes(url.split('#')[1]))
    ), false, 'capabilities must never enter request paths or queries');

    const approved = await page.locator('#save-review').evaluate((form) => ({
      cap: form.elements.cap.value,
      csrf: form.elements.csrf.value,
    }));
    await page.getByRole('button', { name: 'Save change' }).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('heading', { name: 'Saved' }).waitFor();
    assert.equal(await page.getByText('TeamDynamix accepted the change.').isVisible(), true);
    const duplicate = await context.request.post(`${baseURL}/writes/save`, {
      form: approved,
      headers: { Origin: baseURL },
    });
    assert.equal(duplicate.ok(), true, 'duplicate approval POST returns the durable result');
    assert.match(await duplicate.text(), /Saved/);
    status = await (await context.request.get(`${baseURL}/test/status`)).json();
    assert.equal(status.apply_count['1201'], 1, 'duplicate POST must not redispatch');

    // Bind a second pending review to the same browser before a cross-site
    // navigation, then prove the Strict cookie survives and both remain usable.
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto(scenarios.long_edit, { waitUntil: 'networkidle' });
    assert.equal(await page.getByText('Complete ending marker.').isVisible(), true);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert.equal(overflow, false, 'review must not overflow a 320px viewport');
    if (screenshotDir) {
      await page.screenshot({ path: path.join(screenshotDir, 'long-edit-mobile.png'), fullPage: true });
    }

    const beforeCrossSite = (await context.cookies(baseURL)).find(
      (cookie) => cookie.name === '__Host-tdx-write'
    ).value;
    status = await (await context.request.get(`${baseURL}/test/status`)).json();
    const requestCount = status.requests.length;
    const crossSiteURL = baseURL.replace('://127.0.0.1:', '://localhost:');
    const pageB = await context.newPage();
    recordBrowserErrors(pageB);
    await pageB.goto(`${crossSiteURL}/test/status`, { waitUntil: 'networkidle' });
    await pageB.evaluate((url) => {
      document.body.textContent = '';
      const link = document.createElement('a');
      link.href = url;
      link.textContent = 'Open review';
      document.body.append(link);
      link.click();
    }, scenarios.conflict);
    await pageB.getByRole('heading', { name: 'External race conflict' }).waitFor();
    const afterCrossSite = (await context.cookies(baseURL)).find(
      (cookie) => cookie.name === '__Host-tdx-write'
    ).value;
    assert.equal(afterCrossSite, beforeCrossSite, 'cross-site shell must not replace the browser binding');
    status = await (await context.request.get(`${baseURL}/test/status`)).json();
    const crossSiteRequests = status.requests.slice(requestCount);
    const crossSiteGet = crossSiteRequests.find((request) => request.path === '/writes/review');
    const bootstrapPost = crossSiteRequests.find((request) => request.path === '/writes/open');
    assert.equal(crossSiteGet.has_cookie, false, 'Strict cookie stays off the cross-site GET');
    assert.equal(bootstrapPost.has_cookie, true, 'same-origin bootstrap POST receives the Strict cookie');

    await page.getByRole('heading', { name: /UnbrokenTitle/ }).waitFor();
    assert.equal(await page.getByText('Not saved', { exact: true }).isVisible(), true,
      'parallel preview remains bound to the unchanged cookie');
    await page.getByRole('button', { name: 'Save change' }).click();
    await page.getByRole('heading', { name: 'Saved' }).waitFor();
    status = await (await context.request.get(`${baseURL}/test/status`)).json();
    assert.equal(status.apply_count['1202'], 1,
      'the original live tab can still save with the preserved browser binding');

    await pageB.getByRole('button', { name: 'Save change' }).click();
    await pageB.getByRole('heading', { name: 'Conflict' }).waitFor();
    assert.equal(await pageB.getByRole('button', { name: 'Save change' }).count(), 0);

    await page.goto(scenarios.unknown, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Save change' }).click();
    await page.getByRole('heading', { name: 'Outcome unknown' }).waitFor();
    assert.equal(await page.getByText(/do not retry/i).isVisible(), true);
    assert.equal(await page.getByRole('button', { name: 'Save change' }).count(), 0);
    if (screenshotDir) {
      await page.screenshot({ path: path.join(screenshotDir, 'unknown-mobile.png'), fullPage: true });
    }
    status = await (await context.request.get(`${baseURL}/test/status`)).json();
    assert.equal(status.apply_count['1202'], 1, 'parallel edit is applied once');
    assert.equal(status.apply_count['1203'], 0, 'conflict must not dispatch');
    assert.equal(status.apply_count['1204'], 1, 'unknown transport is attempted exactly once');

    assert.deepEqual(browserErrors, [], `browser/CSP errors: ${browserErrors.join('\n')}`);
    process.stdout.write(JSON.stringify({ ok: true, apply_count: status.apply_count }) + '\n');
  } finally {
    if (browser) await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
