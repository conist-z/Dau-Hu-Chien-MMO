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
      "/ws": { target: "ws://localhost:8787", ws: true },
      "/config.json": "http://localhost:8787",
    },
  },
});
