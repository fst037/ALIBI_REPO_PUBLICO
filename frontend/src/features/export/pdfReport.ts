import type { QueryResult, ResultTable } from '../../types'
import { chartToPng } from './chartImage'

/** A white page with dark ink: the report is read on paper or a white viewer, not in the app's dark theme. */
const PAGE_BG: [number, number, number] = [255, 255, 255]
const INK: [number, number, number] = [17, 17, 20]
const MUTED: [number, number, number] = [105, 105, 115]
const ACCENT: [number, number, number] = [14, 159, 110]
const MARGIN = 40
const MAX_TABLE_ROWS = 40

interface Block {
  text: string
  chart: QueryResult['chart']
  table: ResultTable
}

function blocksOf(result: QueryResult): Block[] {
  const findings = result.findings ?? []
  if (findings.length > 1) {
    return findings.map((f) => ({ text: f.text, chart: f.chart ?? null, table: f.table }))
  }
  return [{ text: '', chart: result.chart ?? null, table: result.table }]
}

/**
 * The answer as a PDF: question, the synthesised answer, then each finding's
 * chart and rows. Built in the browser from what is already on screen — the
 * numbers in the file are the ones the user was shown, not a second query.
 * `jspdf` is imported dynamically; it is only needed when someone exports.
 */
export async function buildPdfReport(question: string, result: QueryResult): Promise<Blob> {
  const { jsPDF } = await import('jspdf')
  const doc = new jsPDF({ unit: 'pt', format: 'a4' })
  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const innerW = pageW - MARGIN * 2
  let y = MARGIN

  const paint = () => {
    doc.setFillColor(...PAGE_BG)
    doc.rect(0, 0, pageW, pageH, 'F')
  }
  const ensure = (needed: number) => {
    if (y + needed <= pageH - MARGIN) return
    doc.addPage()
    paint()
    y = MARGIN
  }
  const write = (text: string, size: number, color: [number, number, number], bold = false) => {
    doc.setFont('helvetica', bold ? 'bold' : 'normal')
    doc.setFontSize(size)
    doc.setTextColor(...color)
    for (const line of doc.splitTextToSize(text, innerW) as string[]) {
      ensure(size * 1.4)
      doc.text(line, MARGIN, y + size)
      y += size * 1.4
    }
  }

  paint()
  write('ALIBI', 10, ACCENT, true)
  y += 4
  write(question, 18, INK, true)
  y += 8
  write(result.naturalResponse, 11, INK)
  y += 12

  for (const block of blocksOf(result)) {
    if (block.text) {
      write(block.text, 11, INK, true)
      y += 6
    }
    if (block.chart) {
      const img = await chartToPng(block.chart, block.table, 2, 'print')
      const w = innerW
      const h = Math.min((img.height / img.width) * w, pageH - MARGIN * 2)
      ensure(h)
      doc.addImage(img.dataUrl, 'PNG', MARGIN, y, w, h)
      y += h + 12
    }
    y += 4
    writeTable(block.table)
    y += 16
  }

  function writeTable(table: ResultTable) {
    const cols = table.columns
    if (cols.length === 0) return
    const colW = innerW / cols.length
    const row = (cells: string[], bold: boolean) => {
      doc.setFont('helvetica', bold ? 'bold' : 'normal')
      doc.setFontSize(9)
      doc.setTextColor(...(bold ? MUTED : INK))
      ensure(16)
      cells.forEach((cell, i) => {
        const clipped = doc.splitTextToSize(cell, colW - 6)[0] as string | undefined
        doc.text(clipped ?? '', MARGIN + i * colW, y + 10)
      })
      y += 16
    }
    row(cols.map((c) => c.label), true)
    for (const r of table.rows.slice(0, MAX_TABLE_ROWS)) {
      row(cols.map((c) => String(r[c.key] ?? '')), false)
    }
    if (table.rows.length > MAX_TABLE_ROWS) {
      write(`… y ${table.rows.length - MAX_TABLE_ROWS} filas más (descargá el CSV para verlas todas).`, 9, MUTED)
    }
  }

  return doc.output('blob')
}
