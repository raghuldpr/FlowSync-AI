import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

// Dev: proxy the FlowSync FastAPI backend so /api works same-origin (no CORS).
// Default is the laptop backend; for the hosted copy set FLOWSYNC_API, e.g.
//   $env:FLOWSYNC_API = "https://<service>.catalystappsail.com"; npm run dev
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const target = env.FLOWSYNC_API || 'http://localhost:8000'
  return {
    plugins: [react(), tailwindcss()],
    server: { port: 5173, proxy: { '/api': { target, changeOrigin: true, secure: true } } },
  }
})
