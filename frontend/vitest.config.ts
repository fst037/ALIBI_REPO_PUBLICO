import { configDefaults, defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  // e2e/ is Playwright's (npm run e2e), not vitest's.
  defineConfig({ test: { environment: 'jsdom', globals: false, exclude: [...configDefaults.exclude, 'e2e/**'] } }),
)
