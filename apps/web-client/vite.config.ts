import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server mirrors the nginx edge routing so the app is always same-origin:
//   /api  -> blockchain gateway      /fc -> fact-checking service
//   /tips -> claim ingest worker (public tip line)
const GATEWAY = process.env.GATEWAY_URL ?? 'http://127.0.0.1:8000';
const FACT_CHECK = process.env.FACT_CHECK_URL ?? 'http://127.0.0.1:8002';
const INGEST = process.env.INGEST_URL ?? 'http://127.0.0.1:8003';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: GATEWAY, changeOrigin: false },
      '/fc': { target: FACT_CHECK, rewrite: (p) => p.replace(/^\/fc/, '') },
      '/tips': { target: INGEST, rewrite: () => '/public/tip' },
    },
  },
  build: {
    sourcemap: false,
    // No inline scripts or styles, so the strict CSP in nginx holds.
    assetsInlineLimit: 0,
  },
});
