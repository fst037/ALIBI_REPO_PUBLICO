import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // In dev, route the generated client's /api/v1 calls to the local FastAPI
    // backend (`uvicorn app.main:app --reload` in backend/, port 8000). FastAPI
    // serves under /api/v1, so no path rewrite. In prod, point /api/v1/* at the
    // API Gateway origin (CloudFront behavior) instead of this proxy.
    proxy: {
      '/api/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
