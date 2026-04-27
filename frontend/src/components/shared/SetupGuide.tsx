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
import { useI18n } from "@/i18n"
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
  const { t } = useI18n()
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
          <CardTitle className="text-2xl">{t("shared.setupGuide.title")}</CardTitle>
          <CardDescription>
            {t("shared.setupGuide.description")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {checking ? (
            <div className="flex flex-col items-center py-8">
              <Loader2 className="text-primary mb-3 h-8 w-8 animate-spin" />
              <p className="text-muted-foreground text-sm">{t("shared.setupGuide.checkingEnvironment")}</p>
            </div>
          ) : (
            <>
              {/* Step 1: Install Ollama */}
              <div className="flex items-start gap-3">
                <StepIcon status={ollamaOk ? "done" : "error"} />
                <div className="flex-1">
                  <p className="font-medium">{t("shared.setupGuide.installAndStartOllama")}</p>
                  {ollamaOk ? (
                    <p className="text-muted-foreground text-sm">
                      {t("shared.setupGuide.ollamaRunningAt", { url: env?.ollama_url ?? "" })}
                    </p>
                  ) : (
                    <div className="mt-1 space-y-2">
                      <p className="text-sm text-red-600 dark:text-red-400">
                        {t("shared.setupGuide.ollamaNotDetected")}
                      </p>
                      <p className="text-muted-foreground text-sm">
                        {t("shared.setupGuide.step1Visit")}{" "}
                        <a
                          href="https://ollama.com"
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary inline-flex items-center gap-1 underline"
                        >
                          ollama.com
                          <ExternalLink className="h-3 w-3" />
                        </a>{" "}
                        {t("shared.llmSetup.installDownload")}
                      </p>
                      <p className="text-muted-foreground text-sm">
                        {t("shared.setupGuide.step2StartOllama")}
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
                    {t("shared.setupGuide.downloadModel", {
                      model: env?.recommended_model ?? env?.required_model ?? "qwen3:8b",
                    })}
                  </p>
                  {modelOk ? (
                    <p className="text-muted-foreground text-sm">
                      {t("shared.setupGuide.modelReady")}
                    </p>
                  ) : ollamaOk ? (
                    <div className="mt-1 space-y-2">
                      <p className="text-sm text-red-600 dark:text-red-400">
                        {t("shared.setupGuide.requiredModelMissing")}
                      </p>
                      <p className="text-muted-foreground text-sm">
                        {t("shared.setupGuide.runInTerminal")}
                      </p>
                      <code className="bg-muted block rounded px-3 py-2 text-sm">
                        ollama pull {env?.recommended_model ?? env?.required_model ?? "qwen3:8b"}
                      </code>
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">
                      {t("shared.setupGuide.completePreviousStep")}
                    </p>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center justify-between pt-2">
                <Button variant="outline" onClick={runCheck}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {t("shared.llmSetup.redetect")}
                </Button>
                <Button
                  variant="ghost"
                  className="text-muted-foreground"
                  onClick={onReady}
                >
                  {t("shared.setupGuide.skipForNow")}
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
