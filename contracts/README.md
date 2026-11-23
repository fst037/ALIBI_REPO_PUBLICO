# ALIBI — API contracts

Spec-first source of truth for the ALIBI API. One OpenAPI 3.0.3 file per domain,
composed by a root and bundled for the mock server (Prism) and the frontend
typed client (Orval).

## Layout

```
contracts/
├── root.yaml          ← entry: info/servers/security/tags + $refs to every domain path
├── auth.yaml          ← auth, account, plans, billing, notifications
├── ingestion.yaml     ← datasets + inferred schema (Zero-ETL)
├── chat.yaml          ← conversations, prepare/execute, history, saved queries
├── visualization.yaml ← exports (CSV/PNG/PDF)
├── dashboards.yaml    ← saved charts per dataset
└── openapi.yaml       ← GENERATED bundle (git-ignored) — do not edit
```

**Edit the per-domain files.** `openapi.yaml` is derived; never edit it by hand.

## Workflow (scripts in the repo-root `package.json`)

```bash
npm install                 # once, at the repo root — installs redocly/prism/orval

npm run contracts:bundle    # root.yaml + domains → contracts/openapi.yaml
npm run contracts:lint      # validate the bundle (redocly)
npm run api:generate        # orval → frontend/src/lib/api/generated (typed fetch client)
npm run contracts:build     # bundle → lint → generate, in one go

npm run api:mock            # Prism mock server on http://localhost:4010
```

For local dev: run `npm run api:mock` (repo root) and `npm run dev` (in `frontend/`).
Vite proxies the generated client's `/api/v1/*` calls to Prism (see
`frontend/vite.config.ts`; Prism serves at the root, so the proxy strips the
`/api/v1` prefix).

## Generated client

Orval emits a **typed fetch client** (not react-query hooks) under
`frontend/src/lib/api/generated/`, one module per domain tag plus TS models.
These functions slot in **behind** the `src/lib/api/` seam — screens keep
calling `lib/api`, never the generated code directly. To switch to react-query
hooks, change `client` in `orval.config.ts` (adds `@tanstack/react-query`).

## Conventions

- Shapes mirror `frontend/src/types/index.ts` so generated types match the app.
- Standard error envelope: `{ error: { code, message, details? } }`.
- Auth: bearer JWT (`bearerAuth`); public endpoints set `security: []`.
- Read-only guardrail: NL write attempts → `200` `role: guard`; edited SQL failing
  AST validation → `422`; execution failures → `408`/`402`/`503`.
