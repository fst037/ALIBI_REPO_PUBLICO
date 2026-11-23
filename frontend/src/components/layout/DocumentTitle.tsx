import { useEffect } from 'react'
import { Outlet, useMatches } from 'react-router-dom'

/**
 * What a route puts in its `handle` to name itself in the browser tab.
 * Routes without one (redirects, the landing page) leave the tab as the bare
 * brand.
 */
export interface RouteTitleHandle {
  title?: string
}

const BRAND = 'ALIBI'

/** `useMatches` types `handle` as `unknown` — this is the narrowing. */
function titleOf(handle: unknown): string | undefined {
  if (typeof handle !== 'object' || handle === null) return undefined
  const { title } = handle as RouteTitleHandle
  return typeof title === 'string' && title.length > 0 ? title : undefined
}

/**
 * Keeps `document.title` in step with the route: `ALIBI - Biblioteca`.
 *
 * A pathless layout route at the root of the tree, so it re-runs on every
 * navigation and there is exactly one place that writes the title. The name
 * itself lives on each route's `handle` in `router.tsx`, next to the path it
 * belongs to, rather than in a path→label map that drifts as routes move.
 *
 * The deepest match that names itself wins, so a nested route can be more
 * specific than its parent.
 */
export function DocumentTitle() {
  const matches = useMatches()
  const page = matches.reduce<string | undefined>(
    (deepest, match) => titleOf(match.handle) ?? deepest,
    undefined,
  )

  useEffect(() => {
    document.title = page ? `${BRAND} - ${page}` : BRAND
  }, [page])

  return <Outlet />
}
