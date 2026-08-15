import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API is served from the same origin in production (the backend mounts the
// built frontend), so the dev server proxies /api instead of the app carrying a
// configurable base URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
