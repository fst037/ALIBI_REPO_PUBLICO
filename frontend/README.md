# ALIBI — frontend

The ALIBI web app: React + Vite + TypeScript, served from S3 behind CloudFront.

## Run locally

```bash
npm install
npm run dev        # http://localhost:5173
```

In development, Vite proxies `/api/v1` to the FastAPI backend on `http://localhost:8000` (see `vite.config.ts`), so start the backend first (`backend/README.md`). In production, CloudFront routes `/api/v1/*` to API Gateway.

Optional: set `VITE_GOOGLE_CLIENT_ID` in `frontend/.env.local` to show "Continuar con Google".

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server with hot reload |
| `npm run build` | Type-check (`tsc -b`) and build to `dist/` |
| `npm run lint` | Lint with oxlint |
| `npx vitest` | Run the tests |

## Layout

```
src/
├── app/           One component per screen (routes in router.tsx)
├── components/    UI primitives (ui/) and the app shell (layout/), no business logic
├── features/      Domain logic: conversation, dashboards, ingest, folders, billing, auth…
├── lib/api/       The only way screens reach the backend; generated/ is the Orval client
├── styles/        tokens.css (design tokens) and alibi.css (shared classes)
└── types/         Domain types returned by lib/api
```

The API client in `src/lib/api/generated/` is generated from `contracts/` with Orval — run `npm run contracts:build` from the repository root, and don't edit it by hand. Charts are Vega-Lite specs rendered with `vega-embed` and the ALIBI theme in `components/ui/charts/vegaTheme.ts`.

Deployed by `.github/workflows/frontend-deploy.yml` (`npm ci`, build, `s3 sync`, CloudFront invalidation).
