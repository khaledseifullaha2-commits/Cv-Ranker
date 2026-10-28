import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    target: "es2022",
    outDir: "dist",
    // Never publish source maps: the bundle is public, the sources need not be.
    sourcemap: false,
    // The pdf.js worker must stay a real file (it is fetched as a Worker).
    assetsInlineLimit: 0,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
});
