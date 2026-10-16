import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite-plus";

export default defineConfig(({ command }) => ({
  plugins: [svelte()],
  base: command === "build" ? "/static/" : "/",
  build: {
    outDir: "../src/playground/static",
    emptyOutDir: true,
    license: { fileName: "licenses.md" },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  lint: { options: { typeAware: true, typeCheck: true } },
  test: { include: ["src/**/*.test.ts", "e2e-native/*.test.ts"] },
}));
