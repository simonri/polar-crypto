import react from '@vitejs/plugin-react'
import path from 'node:path'
import tsconfigPaths from 'vite-tsconfig-paths'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [
    tsconfigPaths(),
    react({
      babel: {
        // Orbit's <Box /> is built on StyleX, which must be compiled: without
        // this plugin importing Box throws "Unexpected 'stylex.defineVars'
        // call at runtime" under jsdom.
        plugins: [
          [
            '@stylexjs/babel-plugin',
            {
              dev: true,
              runtimeInjection: false,
              treeshakeCompensation: true,
              unstable_moduleResolution: {
                type: 'commonJS',
                rootDir: path.resolve(__dirname, '../..'),
              },
            },
          ],
        ],
      },
    }),
  ],
  test: {
    environment: 'jsdom',
    exclude: ['**/node_modules/**', '**/dist/**', 'e2e/**'],
    env: {
      NEXT_PUBLIC_FRONTEND_BASE_URL: 'https://polar.sh',
      NEXT_PUBLIC_SANDBOX_FRONTEND_BASE_URL: 'https://sandbox.polar.sh',
    },
  },
})
