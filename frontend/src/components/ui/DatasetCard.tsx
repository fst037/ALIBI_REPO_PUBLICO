import { BracketsCurly, FileCsv, FileDoc, FilePdf, FileXls } from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'
import { Pill } from './Pill'
import type { PillTone } from './Pill'

export type DatasetFileType = 'csv' | 'xlsx' | 'pdf' | 'docx' | 'json'

export interface DatasetStatus {
  tone: PillTone
  label: string
  dot?: boolean
}

export interface DatasetCardProps {
  name: string
  fileType: DatasetFileType
  /** Secondary line, e.g. "12,480 rows · 2 MB · 3 days ago". */
  meta: string
  status?: DatasetStatus
  /** Selected state (`.ds-card.sel`), e.g. multi-select in the library. */
  selected?: boolean
  onClick?: () => void
  className?: string
}

/**
 * Icon per file type. Exported because three screens need the same mapping
 * (Biblioteca, SourcePicker, Ingesta) and a fourth format would otherwise mean
 * finding every copy — as `docx`/`json` did.
 */
export const FILE_ICON: Record<DatasetFileType, PhosphorIcon> = {
  csv: FileCsv,
  xlsx: FileXls,
  pdf: FilePdf,
  docx: FileDoc,
  json: BracketsCurly,
}

/** Dataset tile (`.ds-card`) with file-type icon and status pill. */
export function DatasetCard({
  name,
  fileType,
  meta,
  status,
  selected = false,
  onClick,
  className,
}: DatasetCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx('ds-card', selected && 'sel', className)}
      style={{ textAlign: 'left', width: '100%' }}
    >
      <div className="between">
        <div className={cx('ds-ico', fileType)}>
          <Icon icon={FILE_ICON[fileType]} size={19} />
        </div>
        {status && (
          <Pill tone={status.tone} dot={status.dot}>
            {status.label}
          </Pill>
        )}
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{name}</div>
        <div className="hint" style={{ marginTop: 3 }}>
          {meta}
        </div>
      </div>
    </button>
  )
}
