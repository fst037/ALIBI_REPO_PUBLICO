import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CaretDown, Export, FileCsv, FilePdf, Image } from '@phosphor-icons/react'
import { Icon, Pill, Spinner } from '../../components/ui'
import type { QueryResult } from '../../types'
import { cx } from '../../lib/cx'
import { usePlan } from '../billing/usePlan'
import { downloadBlob, downloadText, slugify, tableToCsv } from './csv'
import { chartToPng } from './chartImage'
import { buildPdfReport } from './pdfReport'
import styles from './ExportMenu.module.css'

export interface ExportMenuProps {
  result: QueryResult
  /** Base filename (usually the question). */
  filenameBase: string
  /** Overrides the plan lookup; by default the caller's plan decides (`exportFormats`). */
  canExportImages?: boolean
  size?: 'sm' | 'md'
}

/** Contextual export menu for a result: CSV (real download) + PNG/PDF (Pro). */
export function ExportMenu({ result, filenameBase, canExportImages, size = 'sm' }: ExportMenuProps) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [busyImg, setBusyImg] = useState<'png' | 'pdf' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { plan } = usePlan()
  const unlocked = canExportImages ?? plan?.limits.exportFormats.includes('png') ?? false
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const exportCsv = () => {
    setBusy(true)
    downloadText(`${slugify(filenameBase)}.csv`, tableToCsv(result.table))
    window.setTimeout(() => {
      setBusy(false)
      setOpen(false)
    }, 500)
  }
  const onImage = async (format: 'png' | 'pdf') => {
    if (!unlocked) {
      setOpen(false)
      navigate('/cuenta?tab=billing')
      return
    }
    if (busyImg) return
    setError(null)
    setBusyImg(format)
    try {
      const name = slugify(filenameBase)
      if (format === 'png') {
        if (!result.chart) throw new Error('Esta respuesta no tiene gráfico para exportar.')
        const img = await chartToPng(result.chart, result.table)
        downloadBlob(`${name}.png`, await (await fetch(img.dataUrl)).blob())
      } else {
        downloadBlob(`${name}.pdf`, await buildPdfReport(filenameBase, result))
      }
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo generar el archivo.')
    } finally {
      setBusyImg(null)
    }
  }

  return (
    <div className={styles.wrap} ref={ref}>
      <button
        type="button"
        className={cx('btn', 'btn-glass', size === 'sm' && 'btn-sm')}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon icon={Export} size="sm" />Exportar<Icon icon={CaretDown} size={13} />
      </button>

      {open && (
        <div className={styles.menu} role="menu">
          <button type="button" className={styles.item} onClick={exportCsv} disabled={busy}>
            <span className={cx(styles.ico, styles.csv)}><Icon icon={FileCsv} size={18} /></span>
            <span className="grow">
              <span className={styles.t}>Datos en CSV</span>
              <span className={styles.d}>La tabla de resultados</span>
            </span>
            {busy && <Spinner />}
          </button>
          <button type="button" className={styles.item} onClick={() => void onImage('png')} disabled={busyImg !== null}>
            <span className={cx(styles.ico, styles.png)}><Icon icon={Image} size={18} /></span>
            <span className="grow">
              <span className={styles.t}>Imagen del gráfico</span>
              <span className={styles.d}>PNG de alta resolución</span>
            </span>
            {busyImg === 'png' ? <Spinner /> : !unlocked && <Pill tone="accent">Pro</Pill>}
          </button>
          <button type="button" className={styles.item} onClick={() => void onImage('pdf')} disabled={busyImg !== null}>
            <span className={cx(styles.ico, styles.pdf)}><Icon icon={FilePdf} size={18} /></span>
            <span className="grow">
              <span className={styles.t}>Informe en PDF</span>
              <span className={styles.d}>Pregunta, tabla y gráfico</span>
            </span>
            {busyImg === 'pdf' ? <Spinner /> : !unlocked && <Pill tone="accent">Pro</Pill>}
          </button>
          {error && <p className="hint" role="alert" style={{ padding: '6px 12px' }}>{error}</p>}
        </div>
      )}
    </div>
  )
}
