import type { Icon as PhosphorIcon, IconProps } from '@phosphor-icons/react'

/** Named sizes map to the px steps used by alibi.css (.i-sm / .i / .i-lg). */
const SIZES = { sm: 15, md: 18, lg: 22, xl: 28 } as const
export type IconSize = keyof typeof SIZES

export interface AppIconProps extends Omit<IconProps, 'size'> {
  icon: PhosphorIcon
  size?: IconSize | number
}

/**
 * Thin wrapper over a Phosphor icon component. Replaces the prototype's
 * `<i class="ph ph-*">` font glyphs with real SVG icons, standardising size
 * and defaulting color to `currentColor` (Phosphor's default).
 */
export function Icon({ icon: Cmp, size = 'md', ...rest }: AppIconProps) {
  return <Cmp size={typeof size === 'number' ? size : SIZES[size]} {...rest} />
}
