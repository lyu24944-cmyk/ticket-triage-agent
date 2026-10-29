import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/review/",
  build: {
    outDir: "../src/ticket_triage/api/static/review",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
