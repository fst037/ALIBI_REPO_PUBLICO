import { expect, test } from '@playwright/test'
import { publicApi, signedInApi, USER } from './mockApi'

// The public entry points, sign-in, and the guard on private screens.

test('the landing page loads and leads to sign-in and sign-up', async ({ page }) => {
  await publicApi().install(page)
  await page.goto('/')
  await expect(page).toHaveTitle('ALIBI')
  await expect(page.locator('h1')).toBeVisible()
  await page.getByRole('link', { name: 'Iniciar sesión' }).first().click()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Te damos la bienvenida' })).toBeVisible()
})

test('a private screen sends a signed-out visitor to sign-in', async ({ page }) => {
  await publicApi().install(page)
  await page.goto('/biblioteca')
  await expect(page).toHaveURL(/\/login$/)
})

test('a malformed email is refused before anything is sent', async ({ page }) => {
  const api = publicApi()
  await api.install(page)
  await page.goto('/login')
  const email = page.getByPlaceholder('vos@empresa.com')
  await email.fill('ana@pyme')
  await page.getByPlaceholder('Tu contraseña').fill('Secreto12')
  await expect(page.getByText('Ingresá un email válido')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Iniciar sesión' })).toBeDisabled()
  expect(api.callsTo('POST', '/auth/login')).toHaveLength(0)
})

test('wrong credentials show the error and keep the user on the form', async ({ page }) => {
  const api = publicApi().on('POST', '/auth/login', {
    status: 401,
    json: { error: { code: 'invalid_credentials', message: 'Invalid credentials' } },
  })
  await api.install(page)
  await page.goto('/login')
  await page.getByPlaceholder('vos@empresa.com').fill(USER.email)
  await page.getByPlaceholder('Tu contraseña').fill('Equivocada1')
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await expect(page.getByText('Email o contraseña incorrectos. Probá de nuevo.')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('signing in opens the app and keeps the session across a reload', async ({ page }) => {
  // Signed out until the login call answers, signed in after it.
  let signedIn = false
  const api = signedInApi()
    .on('GET', '/auth/me', () =>
      signedIn ? { json: USER } : { status: 401, json: { error: { code: 'unauthorized', message: 'Sin sesión' } } },
    )
    .on('POST', '/auth/login', (request) => {
      const body = request.postDataJSON() as { email: string; password: string }
      signedIn = body.email === USER.email && body.password === 'Secreto12'
      return { json: { status: 'authenticated', accessToken: 'e2e-token', tokenType: 'bearer', user: USER } }
    })
  await api.install(page)
  await page.goto('/login')
  await page.getByPlaceholder('vos@empresa.com').fill(USER.email)
  await page.getByPlaceholder('Tu contraseña').fill('Secreto12')
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await expect(page).toHaveURL(/\/app$/)
  await expect(page).toHaveTitle('ALIBI - Conversación')

  await page.reload()
  await expect(page).toHaveURL(/\/app$/)
})
