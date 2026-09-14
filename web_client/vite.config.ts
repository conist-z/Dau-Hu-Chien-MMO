import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
  },
  server: {
    port: 5173,
    // Dev proxy: /ws + /config.json forwarded to a locally running relay.
    proxy: {
      "/ws": { target: "https://web-production-19398.up.railway.app", ws: true, secure: true, changeOrigin: true },
      "/config.json": "https://web-production-19398.up.railway.app",
    },
  },
});
