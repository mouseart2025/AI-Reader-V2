import { useCallback, useEffect, useState } from "react"
import {
  CheckCircle2,
  Circle,
  ExternalLink,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react"
import { checkEnvironment } from "@/api/client"
import type { EnvironmentCheck } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

function StepIcon({ status }: { status: "done" | "error" | "pending" }) {
  if (status === "done")
    return <CheckCircle2 className="h-5 w-5 text-green-500" />
  if (status === "error") return <XCircle className="h-5 w-5 text-red-500" />
  return <Circle className="text-muted-foreground h-5 w-5" />
}

export function SetupGuide({ onReady }: { onReady: () => void }) {
  const [checking, setChecking] = useState(true)
  const [env, setEnv] = useState<EnvironmentCheck | null>(null)

  const runCheck = useCallback(async () => {
    setChecking(true)
    try {
      const data = await checkEnvironment()
      setEnv(data)
      // Cloud mode: no local setup needed
      if (data.llm_provider === "openai") {
        onReady()
        return
      }
      if (data.ollama_running && data.model_available) {
        onReady()
      }
    } catch {
      setEnv(null)
    } finally {
      setChecking(false)
    }
  }, [onReady])

  useEffect(() => {
    runCheck()
  }, [runCheck])

  const ollamaOk = env?.ollama_running ?? false
  const modelOk = env?.model_available ?? false

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-lg">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">环境配置</CardTitle>
          <CardDescription>
            AI Reader 需要本地运行 Ollama 来提供 AI 分析能力
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {checking ? (
            <div className="flex flex-col items-center py-8">
              <Loader2 className="text-primary mb-3 h-8 w-8 animate-spin" />
              <p className="text-muted-foreground text-sm">正在检测环境...</p>
            </div>
          ) : (
            <>
              {/* Step 1: Install Ollama */}
              <div className="flex items-start gap-3">
                <StepIcon status={ollamaOk ? "done" : "error"} />
                <div className="flex-1">
                  <p className="font-medium">安装并启动 Ollama</p>
                  {ollamaOk ? (
                    <p className="text-muted-foreground text-sm">
                      Ollama 服务已运行 ({env?.ollama_url})
                    </p>
                  ) : (
                    <div className="mt-1 space-y-2">
                      <p className="text-sm text-red-600 dark:text-red-400">
                        未检测到 Ollama 服务
                      </p>
                      <p className="text-muted-foreground text-sm">
                        1. 访问{" "}
                        <a
                          href="https://ollama.com"
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary inline-flex items-center gap-1 underline"
                        >
                          ollama.com
                          <ExternalLink className="h-3 w-3" />
                        </a>{" "}
                        下载安装
                      </p>
                      <p className="text-muted-foreground text-sm">
                        2. 安装后启动 Ollama 应用
                      </p>
                    </div>
                  )}
                </div>
              </div>

              {/* Step 2: Download model */}
              <div className="flex items-start gap-3">
                <StepIcon
                  status={
                    modelOk ? "done" : ollamaOk ? "error" : "pending"
                  }
                />
                <div className="flex-1">
                  <p className="font-medium">
                    下载模型 ({env?.required_model ?? "qwen2.5:7b"})
                  </p>
                  {modelOk ? (
                    <p className="text-muted-foreground text-sm">
                      模型已就绪
                    </p>
                  ) : ollamaOk ? (
                    <div className="mt-1 space-y-2">
                      <p className="text-sm text-red-600 dark:text-red-400">
                        所需模型未下载
                      </p>
                      <p className="text-muted-foreground text-sm">
                        在终端运行：
                      </p>
                      <code className="bg-muted block rounded px-3 py-2 text-sm">
                        ollama pull {env?.required_model ?? "qwen2.5:7b"}
                      </code>
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">
                      请先完成上一步
                    </p>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center justify-between pt-2">
                <Button variant="outline" onClick={runCheck}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  重新检测
                </Button>
                <Button
                  variant="ghost"
                  className="text-muted-foreground"
                  onClick={onReady}
                >
                  跳过，稍后配置
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
