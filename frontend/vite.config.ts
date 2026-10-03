import { readFileSync, rmSync } from "node:fs"
import path from "path"
import { defineConfig, type Plugin } from "vitest/config"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { visualizer } from "rollup-plugin-visualizer"

const pkg = JSON.parse(readFileSync("./package.json", "utf-8"))

// 构建后从 dist 剥离纯 web 静态数据：
// - demo-data 只服务 /demo 演示站（build:demo 以 --mode demo 构建，不受影响）
// - sample-data 由后端 sample_data_service 直接读源码目录，前端从不经 HTTP 消费
// 桌面安装包（Tauri 打包 frontendDist=dist）因此不再携带这两份数据
function stripWebOnlyData(): Plugin {
  let outDir = "dist"
  let root = process.cwd()
  return {
    name: "strip-web-only-data",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir
      root = config.root
    },
    closeBundle() {
      for (const dir of ["demo-data", "sample-data"]) {
        rmSync(path.resolve(root, outDir, dir), { recursive: true, force: true })
      }
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    tailwindcss(),
    ...(mode === "demo" ? [] : [stripWebOnlyData()]),
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
          // 只手动拆出全局必用的 React 核心；graph/markdown/d3 等
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
}))
