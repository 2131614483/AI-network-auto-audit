import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: { lib: { entry: resolve("electron/main.ts"), formats: ["cjs"] }, rollupOptions: { output: { entryFileNames: "index.js" } } },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: { lib: { entry: resolve("electron/preload.ts"), formats: ["cjs"] }, rollupOptions: { output: { entryFileNames: "index.js" } } },
  },
  renderer: {
    root: "src",
    plugins: [react()],
    build: { rollupOptions: { input: resolve("src/index.html") } },
    resolve: { alias: { "@": resolve("src") } },
  },
});
