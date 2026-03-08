import { readFileSync } from "node:fs"
import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { visualizer } from "rollup-plugin-visualizer"

const pkg = JSON.parse(readFileSync("./package.json", "utf-8"))

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    ...(process.env.ANALYZE ? [visualizer({ open: false, filename: "dist/stats.html", gzipSize: true })] : []),
  ],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: process.env.TAURI_ENV_PLATFORM === "windows"
      ? "chrome105"
      : process.env.TAURI_ENV_PLATFORM
        ? "safari13"
        : undefined,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("/react-dom/") || id.includes("/react/") || id.includes("/react-router") || id.includes("/scheduler/")) return "vendor-react"
            if (id.includes("/react-force-graph") || id.includes("/force-graph/") || id.includes("/canvas-color-tracker/")) return "vendor-graph"
            if (id.includes("/d3-")) return "vendor-d3"
            if (id.includes("/radix-ui/") || id.includes("/@radix-ui/")) return "vendor-ui"
            if (id.includes("/react-markdown/") || id.includes("/micromark") || id.includes("/mdast-") || id.includes("/remark-") || id.includes("/unified/") || id.includes("/hast-") || id.includes("/unist-")) return "vendor-markdown"
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    host: process.env.TAURI_DEV_HOST || false,
    hmr: process.env.TAURI_DEV_HOST
      ? { protocol: "ws" as const, host: process.env.TAURI_DEV_HOST, port: 5174 }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
})
