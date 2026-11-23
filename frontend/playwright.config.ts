import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end interface tests over the production build (`vite build` →
 * `vite preview`), with the API answered inside the browser (e2e/mockApi.ts).
 *
 * The same specs run on the three engines a user can bring — Chromium (Chrome,
 * Edge), Gecko (Firefox), WebKit (Safari) — and on a phone-sized WebKit, which
 * is what the browser-compatibility claim in the thesis rests on. On the phone
 * only the public screens pass: the signed-in app has no phone layout yet. WebKit here
 * is Playwright's build, close to Safari but not Safari itself.
 */
const PORT = 4173

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    locale: 'es-AR',
    timezoneId: 'America/Argentina/Buenos_Aires',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] }, testIgnore: 'phone.spec.ts' },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] }, testIgnore: 'phone.spec.ts' },
    { name: 'webkit', use: { ...devices['Desktop Safari'] }, testIgnore: 'phone.spec.ts' },
    // The signed-in app has no phone layout yet (see e2e/phone.spec.ts), so on a
    // phone only the public screens are exercised, plus the test that records the gap.
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'] }, testMatch: ['auth.spec.ts', 'phone.spec.ts'] },
  ],
  webServer: {
    command: `npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
  },
})
