import { defineConfig } from "vite";

const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/admin": { target: backend, changeOrigin: true },
      "/healthz": { target: backend, changeOrigin: true },
      "/readyz": { target: backend, changeOrigin: true },
      "/metrics": { target: backend, changeOrigin: true },
    },
  },
});
