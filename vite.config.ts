import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Accept the dynamic Arena preview host. The production container will be served
// behind the Synology host/reverse proxy and will use its own allow-list policy.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      // Browser code always uses relative URLs. In development Vite forwards
      // them to FastAPI; in Docker both API and SPA share port 8080.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
