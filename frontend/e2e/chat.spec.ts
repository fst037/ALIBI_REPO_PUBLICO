import { expect, test, type Page } from '@playwright/test'
import { answerMessage, CONVERSATION, MockApi, run, signedInApi, signIn } from './mockApi'

// Asking a question in a folder's conversation, the
// run's progress, the answer (sentence → chart → rows, SQL behind a control),
// the guardrail, a failure, and Deep being a Pro feature. The chart is drawn by
// the real vega-embed in each browser engine — the part most likely to differ.

const QUESTION = '¿Qué sede vendió más en 2024?'

/** A run that is queued, then running with one step, then done with `final`. */
function answeringRun(api: MockApi, final: Parameters<typeof run>[1]) {
  let polls = 0
  api
    .on('POST', '/conversations', { status: 201, json: CONVERSATION })
    .on('POST', '/conversations/:id/runs', { status: 202, json: run('queued') })
    .on('GET', '/conversations/:id/runs/:runId', () => {
      polls += 1
      if (polls === 1) {
        return {
          json: run('running', {
            steps: [{ id: 's1', label: 'Leyendo tu pregunta', status: 'running', startedAt: '2026-11-20T14:00:01Z' }],
          }),
        }
      }
      return { json: run('done', final) }
    })
  return api
}

async function openFolderChat(page: Page, api: MockApi) {
  await api.install(page)
  await signIn(page)
  await page.goto('/app?folder=f1')
  await expect(page.getByRole('heading', { name: /Listo para responder preguntas sobre/ })).toBeVisible()
}

async function ask(page: Page, question = QUESTION) {
  const box = page.getByPlaceholder('Preguntá sobre tus datos…')
  await box.fill(question)
  await box.press('Enter')
}

test('an answer shows the sentence, a drawn chart, and the SQL and rows on demand', async ({ page }) => {
  const api = answeringRun(signedInApi('pro'), { message: answerMessage(QUESTION) })
  await openFolderChat(page, api)
  await ask(page)

  await expect(page.getByText('Palermo es la sede que más vendió en 2024')).toBeVisible()

  // vega-embed renders to SVG: one bar per row, and the axis titles from the spec.
  const chart = page.locator('svg.marks')
  await expect(chart).toBeVisible()
  await expect(chart.locator('.mark-rect path')).toHaveCount(3)
  await expect(chart.getByText('Sede', { exact: true })).toBeVisible()

  // With a chart, the rows start closed.
  const rows = page.getByRole('button', { name: /Datos · 3 filas/ })
  await expect(rows).toHaveAttribute('aria-expanded', 'false')
  await rows.click()
  await expect(page.getByRole('cell', { name: 'Palermo' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '1,520,000' })).toBeVisible()

  // The SQL is formatted for reading, and stays valid (>= is not split).
  await page.getByRole('button', { name: /SQL generado/ }).click()
  const sql = page.locator('.sql-body')
  await expect(sql).toContainText("WHERE fecha >= '2024-01-01'")
  await expect(sql).toContainText('GROUP BY sede')

  await page.getByRole('button', { name: /Cómo llegué a esto · 2 pasos/ }).click()
  await expect(page.getByText('Sumo el total por sede para compararlas.')).toBeVisible()

  const start = api.callsTo('POST', '/conversations/:id/runs')
  expect(start).toHaveLength(1)
})

test('the question goes to the run with the conversation depth', async ({ page }) => {
  const api = answeringRun(signedInApi('pro'), { message: answerMessage(QUESTION) })
  const bodies: unknown[] = []
  api.on('POST', '/conversations/:id/runs', (request) => {
    bodies.push(request.postDataJSON())
    return { status: 202, json: run('queued') }
  })
  await openFolderChat(page, api)
  await ask(page)
  await expect(page.getByText('Palermo es la sede que más vendió en 2024')).toBeVisible()
  expect(bodies[0]).toMatchObject({ question: QUESTION })
  expect((bodies[0] as { mode?: string }).mode ?? 'quick').toBe('quick')
})

test('Ctrl+Enter asks one question in Profunda on Pro', async ({ page }) => {
  const api = answeringRun(signedInApi('pro'), { message: answerMessage(QUESTION) })
  const modes: unknown[] = []
  api.on('POST', '/conversations/:id/runs', (request) => {
    modes.push((request.postDataJSON() as { mode?: string }).mode)
    return { status: 202, json: run('queued') }
  })
  await openFolderChat(page, api)
  const box = page.getByPlaceholder('Preguntá sobre tus datos…')
  await box.fill(QUESTION)
  await box.press('Control+Enter')
  await expect(page.getByText('Palermo es la sede que más vendió en 2024')).toBeVisible()
  expect(modes).toEqual(['deep'])
})

test('on Free, Profunda is locked and points to the plans', async ({ page }) => {
  await openFolderChat(page, signedInApi('free'))
  await expect(page.getByRole('tab', { name: 'Profunda · Pro' })).toBeDisabled()
  await page.getByRole('link', { name: /Profunda con Pro/ }).click()
  await expect(page).toHaveURL(/\/cuenta\?tab=billing$/)
})

test('a write attempt is stopped by the guardrail with its reason', async ({ page }) => {
  const api = answeringRun(signedInApi('pro'), {
    guard: { status: 'blocked', question: 'Borrá las ventas', message: 'ALIBI solo lee tus datos: no puede borrarlos.' },
  })
  await openFolderChat(page, api)
  await ask(page, 'Borrá las ventas de 2023')
  await expect(page.getByText('ALIBI solo lee tus datos: no puede borrarlos.')).toBeVisible()
  await expect(page.locator('svg.marks')).toHaveCount(0)
})

test('a failed run shows the server message instead of spinning forever', async ({ page }) => {
  const api = answeringRun(signedInApi('pro'), {
    error: { code: 'planner_unavailable', message: 'El asistente no está disponible en este momento.' },
  })
  api.on('GET', '/conversations/:id/runs/:runId', {
    json: run('failed', { error: { code: 'planner_unavailable', message: 'El asistente no está disponible en este momento.' } }),
  })
  await openFolderChat(page, api)
  await ask(page)
  await expect(page.getByText('El asistente no está disponible en este momento.')).toBeVisible()
})
