import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Сборка кладётся в web/dist и раздаётся FastAPI как /static (§18.2) либо
// отдельным nginx-контейнером (web/Dockerfile). base "./" — чтобы работало
// и из корня домена, и из подпути.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    // В режиме VITE_API_MODE=sse фронт ходит на живой webapp.py (§18.2).
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Мок-база (src/mock/kb.json) — отдельный ленивый чанк: в SSE-режиме
    // она не загружается вовсе.
    chunkSizeWarningLimit: 900,
  },
});
