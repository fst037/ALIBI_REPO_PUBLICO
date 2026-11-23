import { defineConfig } from 'orval'

/**
 * Orval codegen for the ALIBI frontend.
 *
 * Generates a TYPED FETCH CLIENT (not react-query hooks): one module per
 * domain tag, each exporting typed functions (`login`, `listDatasets`, …) plus
 * the TypeScript models. This matches the existing `src/lib/api/` seam — the
 * generated functions slot in BEHIND lib/api, so screens keep calling lib/api
 * and never touch generated code directly.
 *
 * To switch to react-query hooks instead, change `client: 'fetch'` to
 * `client: 'react-query'` — but that adds @tanstack/react-query as a runtime
 * dependency and a QueryClientProvider, changing how every screen fetches.
 *
 * Input is the bundled spec, so run `npm run contracts:bundle` first
 * (or `npm run contracts:build`, which chains bundle → lint → generate).
 */
export default defineConfig({
  alibi: {
    input: {
      target: './contracts/openapi.yaml',
    },
    output: {
      mode: 'tags-split',
      target: './frontend/src/lib/api/generated',
      schemas: './frontend/src/lib/api/generated/model',
      client: 'fetch',
      baseUrl: '/api/v1',
      clean: true,
      // The frontend uses oxlint, not prettier; skip Orval's prettier pass.
      prettier: false,
      override: {},
    },
  },
})
