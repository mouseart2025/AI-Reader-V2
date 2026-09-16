import { Suspense } from "react"
import { useRouteError, Link } from "react-router-dom"

export function SuspenseWrapper({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
          加载中...
        </div>
      }
    >
      {children}
    </Suspense>
  )
}

/** Error boundary for demo routes — provides a helpful message and homepage escape */
export function DemoErrorBoundary() {
  const error = useRouteError()
  const landingUrl = (import.meta.env.BASE_URL ?? "/").replace(/\/demo\/?$/, "/") || "/"
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-950 px-6 text-center">
      <span className="mb-4 text-5xl">😵</span>
      <h1 className="mb-2 text-xl font-bold text-white">Demo 页面加载出错</h1>
      <p className="mb-6 text-sm text-slate-400">
        {error instanceof Error ? error.message : "页面未找到或加载失败"}
      </p>
      <div className="flex gap-3">
        <a
          href={landingUrl}
          className="rounded-md bg-blue-500 px-6 py-2 text-sm font-semibold text-white hover:bg-blue-600 transition"
        >
          返回首页
        </a>
        <Link
          to="/demo/honglou/graph"
          className="rounded-md border border-slate-600 px-6 py-2 text-sm font-semibold text-slate-300 hover:border-blue-500 hover:text-white transition"
        >
          打开红楼梦 Demo
        </Link>
      </div>
    </div>
  )
}
