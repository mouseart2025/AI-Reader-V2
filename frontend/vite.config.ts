import { readFileSync } from "node:fs"
import path from "path"
import { defineConfig } from "vitest/config"
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
          // 只手动拆出全局必用的 React 核心；graph/markdown/d3/maplibre 等
          // 交给路由懒加载自动分包——子串匹配会把共享 CJS interop 错归 chunk，
          // 导致入口静态依赖 vendor-graph/vendor-markdown
          if (id.includes("node_modules")) {
            if (id.includes("/react-dom/") || id.includes("/react/") || id.includes("/react-router") || id.includes("/scheduler/")) return "vendor-react"
          }
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
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
