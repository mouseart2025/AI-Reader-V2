import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  fetchEmbeddingConfig,
  saveEmbeddingConfig,
  type EmbeddingConfigResponse,
} from "@/api/client"

/**
 * Issue #20: let low-spec machines offload embeddings to a custom channel.
 * Kept as a standalone component so it does not touch the shared types.ts.
 */
export function EmbeddingConfigCard() {
  const [cfg, setCfg] = useState<EmbeddingConfigResponse | null>(null)
  const [provider, setProvider] = useState("local")
  const [baseUrl, setBaseUrl] = useState("")
  const [model, setModel] = useState("")
  const [dimensions, setDimensions] = useState(0)
  const [apiKey, setApiKey] = useState("")
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState("")
  const [indexWarning, setIndexWarning] = useState(false)

  useEffect(() => {
    fetchEmbeddingConfig()
      .then((d) => {
        setCfg(d)
        setProvider(d.provider || "local")
        setBaseUrl(d.base_url || "")
        setModel(d.model || "")
        setDimensions(d.dimensions || 0)
      })
      .catch(() => setMsg("读取配置失败"))
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setMsg("")
    setIndexWarning(false)
    try {
      const r = await saveEmbeddingConfig({
        provider, base_url: baseUrl, model, api_key: apiKey, dimensions,
      })
      setApiKey("")
      setIndexWarning(r.index_warning)
      setMsg(r.index_warning ? "已保存，但向量索引需重建" : "已保存")
      setCfg(await fetchEmbeddingConfig())
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }, [provider, baseUrl, model, apiKey, dimensions])

  const remote = provider !== "local"

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-medium">Embeddings 配置</h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          低配机器可不加载本地向量模型，改用自定义渠道。API Key 存在本地密钥存储，不写入配置文件。
        </p>
      </div>

      <div>
        <span className="text-sm block mb-1.5">提供方</span>
        <select
          className="w-full border rounded px-2 py-1.5 text-sm bg-background"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        >
          <option value="local">本地模型（BGE / ONNX 回退）</option>
          <option value="openai">OpenAI 兼容端点</option>
          <option value="ollama">Ollama</option>
        </select>
      </div>

      {remote && (
        <>
          <div>
            <span className="text-sm block mb-1.5">Base URL</span>
            <input
              className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={provider === "ollama" ? "http://localhost:11434" : "https://api.example.com/v1"}
            />
          </div>
          <div>
            <span className="text-sm block mb-1.5">模型名</span>
            <input
              className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="text-embedding-3-small"
            />
          </div>
          <div>
            <span className="text-sm block mb-1.5">维度（可选，0 = 用渠道默认值）</span>
            <input
              type="number"
              min={0}
              className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
              value={dimensions}
              onChange={(e) => setDimensions(Number(e.target.value) || 0)}
            />
          </div>
          {provider === "openai" && (
            <div>
              <span className="text-sm block mb-1.5">API Key</span>
              <input
                type="password"
                className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={cfg?.has_api_key ? cfg.api_key_masked : "sk-..."}
              />
            </div>
          )}
        </>
      )}

      <div className="flex items-center gap-3 pt-1">
        <Button size="xs" onClick={handleSave} disabled={saving || (provider === "openai" && (!baseUrl || (!apiKey && !cfg?.has_api_key)))}>
          {saving ? "保存中..." : "保存配置"}
        </Button>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>

      {indexWarning && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          嵌入空间已变化，已建立的向量索引与新模型不兼容。请重建索引后再使用语义搜索（自动重建将在后续版本提供）。
        </p>
      )}
    </div>
  )
}
