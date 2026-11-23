import { expect, test } from '@playwright/test'
import { signedInApi, signIn } from './mockApi'

// Phones (the `mobile-webkit` project runs this file and auth.spec.ts).
//
// KNOWN GAP, found by this suite on 2026-11-22: the signed-in app has no phone
// layout. `.sidebar` (alibi.css) is a fixed 262 px column that never collapses,
// so on a 390 px screen the content gets ~128 px and an answer's text renders 0 px
// wide — in Chromium as much as in WebKit, so it is the layout, not an engine.
// `.app { overflow: hidden }` hides it from a horizontal-scroll check: nothing
// scrolls, it is crushed. The public screens (landing, sign-in, sign-up, legal)
// do have phone breakpoints and pass.
//
// `test.fail` keeps the suite green while saying so; once the shell gets a
// phone layout this test passes, Playwright reports it as an unexpected pass,
// and the `test.fail` line should be removed.

const SCREENS = ['/app', '/biblioteca', '/historial', '/dashboards', '/cuenta']

test('the signed-in app leaves a phone most of its width for content', async ({ page }) => {
  test.fail(true, 'No phone layout for the app shell yet: the 262 px sidebar never collapses.')
  await signedInApi('pro').install(page)
  await signIn(page)
  for (const path of SCREENS) {
    await page.goto(path)
    const main = page.locator('main.main')
    await expect(main).toBeVisible()
    const ratio = await main.evaluate((el) => el.getBoundingClientRect().width / window.innerWidth)
    expect(ratio, `${path}: share of the screen width the content gets`).toBeGreaterThan(0.85)
  }
})
