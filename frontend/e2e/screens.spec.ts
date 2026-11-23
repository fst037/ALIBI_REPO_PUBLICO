import { expect, test } from '@playwright/test'
import { signedInApi, signIn } from './mockApi'

// Every main screen opens without a script error and fits the viewport: no
// horizontal page scroll, which is the first thing to break on a phone or in an
// engine that lays out a flex/grid rule differently.

const SCREENS: [path: string, title: string][] = [
  ['/app', 'ALIBI - Conversación'],
  ['/biblioteca', 'ALIBI - Biblioteca'],
  ['/historial', 'ALIBI - Chats'],
  ['/dashboards', 'ALIBI - Dashboards'],
  ['/cuenta', 'ALIBI - Cuenta'],
  ['/ayuda', 'ALIBI - Ayuda y soporte'],
  ['/ingesta', 'ALIBI - Subir un archivo'],
]

for (const [path, title] of SCREENS) {
  test(`${path} opens cleanly and fits the viewport`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const api = signedInApi('pro')
    await api.install(page)
    await signIn(page)

    await page.goto(path)
    await expect(page).toHaveTitle(title)
    await expect(page).toHaveURL(new RegExp(`${path.replace('/', '\\/')}`))
    await page.waitForLoadState('networkidle')

    expect(errors).toEqual([])
    expect(api.unhandled).toEqual([])
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow, 'horizontal page scroll in px').toBeLessThanOrEqual(0)
  })
}
