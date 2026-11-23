import type { ResultTable } from '../../types'

function esc(v: string | number): string {
  const s = String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Serialize a results table to CSV. */
export function tableToCsv(table: ResultTable): string {
  const header = table.columns.map((c) => esc(c.label)).join(',')
  const rows = table.rows.map((r) => table.columns.map((c) => esc(r[c.key] ?? '')).join(','))
  return [header, ...rows].join('\n')
}

/** Filesystem-safe slug from a question/title. */
export function slugify(s: string): string {
  const base = s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 50)
  return base || 'export'
}

/** Trigger a browser download of a text file (real client-side export). */
export function downloadText(filename: string, text: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob(['﻿' + text], { type: mime }) // BOM so Excel reads UTF-8
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Trigger a browser download of a binary file (PNG, PDF). */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
