import { useId } from 'react'

/* ============================================================
   ALIBI — brand mark
   ------------------------------------------------------------
   An open ring with three ascending measures inside it, the
   tallest one breaking out through the ring's opening.

   The ring is the conversation: one continuous loop over one
   folder, never closed. The measures are the answer the loop
   produces, and the one that escapes is the insight leaving the
   chat — which is what a saved chart on a dashboard is.

   Abstract on purpose: no letterform, no speech bubble, no
   database cylinder. Drawn from tokens.css (--data-1 mint →
   --data-2 cyan) so it sits in the same palette every chart
   does. Reads down to 16px: the ring silhouette plus one
   breaking stroke survive the favicon.
   ============================================================ */

export interface AlibiMarkProps {
  /** Rendered edge length in px. The mark is square. */
  size?: number
  /** Mint→cyan gradient (default) or a single flat `currentColor`. */
  tone?: 'gradient' | 'mono'
  /** Soft accent glow behind the mark. Off for dense/small UI. */
  glow?: boolean
  className?: string
}

/** The ALIBI symbol on its own — no wordmark, no tile, no padding. */
export function AlibiMark({
  size = 28,
  tone = 'gradient',
  glow = true,
  className,
}: AlibiMarkProps) {
  /* useId keeps the gradient/filter refs unique when several marks share a page. */
  const uid = useId().replace(/:/g, '')
  const grad = `alibi-mark-g-${uid}`
  const stroke = tone === 'gradient' ? `url(#${grad})` : 'currentColor'

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      role="img"
      aria-label="ALIBI"
      style={glow ? { filter: 'drop-shadow(0 3px 12px var(--accent-glow))' } : undefined}
    >
      {tone === 'gradient' && (
        <defs>
          <linearGradient id={grad} x1="8" y1="40" x2="40" y2="6" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="var(--data-1)" />
            <stop offset="0.55" stopColor="var(--data-6)" />
            <stop offset="1" stopColor="var(--data-2)" />
          </linearGradient>
        </defs>
      )}

      {/* The conversation: a 270° ring, open toward the top-right. */}
      <path
        d="M37.95 20.78 A14 14 0 1 1 22.78 8.05"
        stroke={stroke}
        strokeWidth="5"
        strokeLinecap="round"
      />

      {/* The answer: three ascending measures on a shared baseline. */}
      <path
        d="M18.5 29 V26.5"
        stroke={stroke}
        strokeWidth="4"
        strokeLinecap="round"
        opacity="0.65"
      />
      <path
        d="M24 29 V22.5"
        stroke={stroke}
        strokeWidth="4"
        strokeLinecap="round"
        opacity="0.85"
      />
      {/* The one that leaves — it clears the ring through the gap. */}
      <path
        d="M29.5 29 V7.5"
        stroke={stroke}
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  )
}

export interface AlibiLogoProps extends AlibiMarkProps {
  /** Wordmark size in px. Defaults to a ratio of the mark. */
  fontSize?: number
  /** Mark only, no wordmark. */
  wordmark?: boolean
}

/** Mark + ALIBI wordmark, locked up horizontally. */
export function AlibiLogo({
  size = 28,
  fontSize,
  wordmark = true,
  tone = 'gradient',
  glow = true,
  className,
}: AlibiLogoProps) {
  return (
    <span
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: Math.round(size * 0.3),
        fontWeight: 600,
        fontSize: fontSize ?? Math.round(size * 0.62),
        letterSpacing: '0.02em',
        color: 'var(--fg-1)',
        lineHeight: 1,
      }}
    >
      <AlibiMark size={size} tone={tone} glow={glow} />
      {wordmark && 'ALIBI'}
    </span>
  )
}
