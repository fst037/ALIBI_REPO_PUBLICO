/* ============================================================
   The API, answered inside the browser.

   These tests check the interface — that it renders, navigates and behaves
   the same in Chromium, Firefox and WebKit — not the backend or the models,
   which have their own suites (backend/tests, backend/eval). So every
   `/api/v1/*` request is fulfilled here with fixtures typed against the
   generated contract models: a contract change breaks these files at type
   check instead of drifting silently.

   This is not a fake mode of the product ("nothing is faked"):
   it exists only in the test browser, and the app's code is the production
   build, unchanged.
   ============================================================ */

import type { Page, Request } from '@playwright/test'
import type {
  AssistantMessage,
  Conversation,
  Dataset,
  Folder,
  FolderDetail,
  Plan,
  QueryResult,
  Run,
  Subscription,
  User,
} from '../src/lib/api/generated/model'

export const NOW = '2026-11-20T14:00:00Z'

export const USER: User = { id: 'u1', name: 'Ana Pérez', email: 'ana@pyme.com.ar', workspace: 'Panadería Sur' }

const limits = (pro: boolean): Plan['limits'] => ({
  datasets: pro ? null : 3,
  queriesPerMonth: pro ? 250 : 20,
  rowsPerQuery: pro ? null : 10000,
  maxFileMb: pro ? 100 : 10,
  historyDays: pro ? null : 30,
  exportFormats: pro ? ['csv', 'png', 'pdf'] : ['csv'],
  answerModes: pro ? ['quick', 'deep'] : ['quick'],
})

const plan = (id: 'free' | 'pro'): Plan => ({
  id,
  name: id === 'pro' ? 'Pro' : 'Free',
  currency: 'ARS',
  priceMonthly: id === 'pro' ? 29000 : 0,
  priceAnnual: id === 'pro' ? 278400 : 0,
  overagePricePerQuery: id === 'pro' ? 116 : null,
  priceMonthlyUsd: id === 'pro' ? 20 : 0,
  priceAnnualUsd: id === 'pro' ? 192 : 0,
  overagePricePerQueryUsd: id === 'pro' ? 0.08 : null,
  usdArsRate: 1450,
  usdArsRateAsOf: '2026-11-20',
  recommended: id === 'pro',
  features: [],
  limits: limits(id === 'pro'),
})

export const PLANS: Plan[] = [plan('free'), plan('pro')]

export function subscription(planId: 'free' | 'pro', queriesUsed = 3): Subscription {
  const l = limits(planId === 'pro')
  return {
    planId,
    cycle: 'monthly',
    overage: { available: planId === 'pro', enabled: false, capArs: null, pricePerQueryArs: null, queries: 0, amountArs: 0 },
    usage: {
      queries: { used: queriesUsed, limit: l.queriesPerMonth, label: 'Consultas' },
      datasets: { used: 1, limit: l.datasets, label: 'Archivos' },
      rowsPerQuery: { used: 0, limit: l.rowsPerQuery, label: 'Filas por consulta' },
    },
    paymentMethod: null,
    invoices: [],
  }
}

export const DATASET: Dataset = {
  id: 'd1',
  name: 'ventas_2024.csv',
  fileType: 'csv',
  tableName: 'ventas_2024',
  folderIds: ['f1'],
  rows: 1200,
  columns: 5,
  sizeLabel: '48 KB',
  meta: '1.200 filas · 5 columnas',
  favorite: false,
  recent: true,
  status: 'processed',
  createdAt: NOW,
  updatedAt: NOW,
}

export const FOLDER: Folder = { id: 'f1', name: 'Ventas', datasetCount: 1, createdAt: NOW, updatedAt: NOW }
export const FOLDER_DETAIL: FolderDetail = { ...FOLDER, datasets: [DATASET] }

export const CONVERSATION: Conversation = {
  id: 'c1',
  title: 'Ventas',
  source: { type: 'folder', id: 'f1', name: 'Ventas' },
  datasetIds: ['d1'],
  createdAt: NOW,
  mode: 'quick',
  messages: [],
}

/** An answer as the harness returns it: one finding, a real Vega-Lite spec, two trail steps. */
export const RESULT: QueryResult = {
  naturalResponse: 'Palermo es la sede que más vendió en 2024, con $ 1,520,000.',
  sql: "SELECT sede, SUM(total) AS ventas FROM ventas_2024 WHERE fecha >= '2024-01-01' GROUP BY sede ORDER BY ventas DESC",
  table: {
    columns: [
      { key: 'sede', label: 'Sede', align: 'left' },
      { key: 'ventas', label: 'Ventas', align: 'num' },
    ],
    rows: [
      { sede: 'Palermo', ventas: 1520000 },
      { sede: 'Belgrano', ventas: 980000 },
      { sede: 'Caballito', ventas: 610000 },
    ],
  },
  rowCount: 3,
  rowsLabel: '3 filas',
  chart: {
    chartType: 'bar',
    spec: {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      mark: { type: 'bar' },
      encoding: {
        x: { field: 'sede', type: 'nominal', title: 'Sede', sort: '-y' },
        y: { field: 'ventas', type: 'quantitative', title: 'Ventas' },
      },
    },
    columns: [
      { name: 'sede', dtype: 'nominal', cardinality: 3 },
      { name: 'ventas', dtype: 'quantitative', cardinality: 3 },
    ],
    source: 'model',
  },
  findings: [],
  trail: [
    { label: 'Columnas de ventas_2024', purpose: 'context', status: 'done', tool: 'describe', rowCount: 5 },
    {
      label: 'Ventas por sede',
      purpose: 'insight',
      status: 'done',
      tool: 'sql',
      thought: 'Sumo el total por sede para compararlas.',
      sql: "SELECT sede, SUM(total) AS ventas FROM ventas_2024 WHERE fecha >= '2024-01-01' GROUP BY sede ORDER BY ventas DESC",
      rowCount: 3,
    },
  ],
}

export function answerMessage(question: string, result: QueryResult = RESULT): AssistantMessage {
  return { id: 'm2', role: 'assistant', question, result, at: NOW }
}

export function run(status: Run['status'], extra: Partial<Run> = {}): Run {
  return {
    id: 'r1',
    conversationId: 'c1',
    question: '¿Qué sede vendió más?',
    isSql: false,
    mode: 'quick',
    status,
    steps: [],
    createdAt: NOW,
    updatedAt: NOW,
    ...extra,
  }
}

type Reply = { status?: number; json?: unknown }
type Handler = (request: Request, match: RegExpMatchArray) => Reply | Promise<Reply>
interface Route {
  method: string
  path: RegExp
  handler: Handler
}

/** Request log and handler table for one page. Later `on` calls win over earlier ones. */
export class MockApi {
  readonly calls: string[] = []
  readonly unhandled: string[] = []
  private routes: Route[] = []

  on(method: string, path: string, handler: Handler | Reply): this {
    const pattern = new RegExp(`^/api/v1${path.replace(/:[a-zA-Z]+/g, '([^/]+)')}$`)
    this.routes.unshift({ method, path: pattern, handler: typeof handler === 'function' ? handler : () => handler })
    return this
  }

  /** Every recorded call to `method path` (path as written to `on`, params matched). */
  callsTo(method: string, path: string): string[] {
    const pattern = new RegExp(`^${method} /api/v1${path.replace(/:[a-zA-Z]+/g, '[^/]+')}$`)
    return this.calls.filter((c) => pattern.test(c))
  }

  async install(page: Page): Promise<void> {
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const { pathname } = new URL(request.url())
      const key = `${request.method()} ${pathname}`
      this.calls.push(key)
      for (const r of this.routes) {
        const match = r.method === request.method() ? pathname.match(r.path) : null
        if (!match) continue
        const reply = await r.handler(request, match)
        return route.fulfill({ status: reply.status ?? 200, json: reply.json ?? {} })
      }
      this.unhandled.push(key)
      return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: `No mock for ${key}` } } })
    })
  }
}

/** A signed-out visitor: only the public calls answer. */
export function publicApi(): MockApi {
  return new MockApi()
    .on('GET', '/plans', { json: PLANS })
    .on('GET', '/auth/me', { status: 401, json: { error: { code: 'unauthorized', message: 'Sin sesión' } } })
}

/** A signed-in user on `planId` with one folder holding one file. */
export function signedInApi(planId: 'free' | 'pro' = 'pro'): MockApi {
  return new MockApi()
    .on('GET', '/plans', { json: PLANS })
    .on('GET', '/plans/:id', (_r, m) => ({ json: PLANS.find((p) => p.id === m[1]) }))
    .on('GET', '/auth/me', { json: USER })
    .on('GET', '/account', { json: USER })
    .on('GET', '/subscription', { json: subscription(planId) })
    .on('GET', '/notifications', { json: [] })
    .on('GET', '/notifications/unread-count', { json: { count: 0 } })
    .on('GET', '/conversations', { json: [] })
    .on('GET', '/conversations/suggestions', { json: [] })
    .on('GET', '/history', { json: [] })
    .on('GET', '/folders', { json: [FOLDER] })
    .on('GET', '/folders/:id', { json: FOLDER_DETAIL })
    .on('GET', '/datasets', { json: [DATASET] })
    .on('GET', '/dashboards', { json: [] })
    .on('GET', '/saved-queries', { json: [] })
}

/** Store a session token before the app boots, as a remembered sign-in would. */
export async function signIn(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('alibi.session.token', 'e2e-token'))
}
