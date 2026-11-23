import type {
  ChartLayout,
  ChartType,
  Dashboard,
  DashboardChart,
  DashboardChartData,
  DashboardDetail,
  SavedChart,
} from '../../types'
import * as gen from './generated/dashboards/dashboards'
import { authInit, unwrap } from './http'

/* ---- Boards ---- */

export function listDashboards(): Promise<Dashboard[]> {
  return unwrap<Dashboard[]>(gen.listDashboards(authInit()))
}

export function createDashboard(name: string, folderId: string): Promise<Dashboard> {
  return unwrap<Dashboard>(gen.createDashboard({ name, folderId }, authInit()))
}

/** The board and its charts — specs and positions, **no data**. */
export function getDashboard(id: string): Promise<DashboardDetail> {
  return unwrap<DashboardDetail>(gen.getDashboard(id, authInit()))
}

export function renameDashboard(id: string, name: string): Promise<Dashboard> {
  return unwrap<Dashboard>(gen.updateDashboard(id, { name }, authInit()))
}

export function deleteDashboard(id: string): Promise<void> {
  return unwrap<void>(gen.deleteDashboard(id, authInit()))
}

/* ---- Charts on a board ---- */

/** Charts pinned from conversations over this board's folder — the pool. */
export function listAvailableCharts(dashboardId: string): Promise<SavedChart[]> {
  return unwrap<SavedChart[]>(gen.listAvailableCharts(dashboardId, authInit()))
}

/**
 * Add a chart, from a pin (`savedChartId`) or straight from an answer
 * (`messageId`). The server copies the idea — `sql` + `spec` — onto the board.
 */
export function addDashboardChart(
  dashboardId: string,
  source: { savedChartId?: string; messageId?: string; title?: string },
): Promise<DashboardChart> {
  return unwrap<DashboardChart>(gen.addDashboardChart(dashboardId, source, authInit()))
}

export function removeDashboardChart(dashboardId: string, chartId: string): Promise<void> {
  return unwrap<void>(gen.removeDashboardChart(dashboardId, chartId, authInit()))
}

/**
 * Re-run one chart's query against the folder's current data.
 *
 * This is what makes a board live. It resolves even when the chart's query has
 * gone stale — that comes back as `status: 'failed'` with a message for the
 * card, because one chart going out of date is not a failure of the request.
 */
export function getDashboardChartData(
  dashboardId: string,
  chartId: string,
): Promise<DashboardChartData> {
  return unwrap<DashboardChartData>(gen.getDashboardChartData(dashboardId, chartId, authInit()))
}

/** Persist the whole arrangement after a drag or resize. */
export function setDashboardLayout(
  dashboardId: string,
  layout: Record<string, ChartLayout>,
): Promise<DashboardDetail> {
  return unwrap<DashboardDetail>(gen.setDashboardLayout(dashboardId, { layout }, authInit()))
}

/* ---- Saved charts (the pool, pinned from a conversation) ---- */

export function listSavedCharts(datasetId: string): Promise<SavedChart[]> {
  return unwrap<SavedChart[]>(gen.listSavedCharts(datasetId, authInit()))
}

/**
 * Pin a chart from a conversation. The server copies the result from the
 * assistant message (`messageId`), so only the reference + chosen type travel.
 * `findingIndex` picks one finding of a multi-finding answer; omitted, the lead.
 */
export function saveChart(
  datasetId: string,
  messageId: string,
  chartType: ChartType,
  findingIndex?: number,
): Promise<SavedChart> {
  return unwrap<SavedChart>(
    gen.saveChart(datasetId, { messageId, chartType, findingIndex }, authInit()),
  )
}

export function removeSavedChart(id: string): Promise<void> {
  return unwrap<void>(gen.removeSavedChart(id, authInit()))
}
