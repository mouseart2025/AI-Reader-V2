import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("AI_READER_DATA_DIR", Path.home() / ".ai-reader-v2"))
DB_PATH = DATA_DIR / "data.db"
CHROMA_DIR = DATA_DIR / "chroma"
GEONAMES_DIR = DATA_DIR / "geonames"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")

# LLM Provider: "ollama" (default, local) or "openai" (cloud, OpenAI-compatible)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

# Cloud LLM settings (used when LLM_PROVIDER="openai")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))

# API protocol format for cloud providers: "openai" (default) | "anthropic"
# Separate from LLM_PROVIDER so users can use Anthropic-compatible proxies
LLM_PROVIDER_FORMAT: str = "openai"

# QA mode: "rag" (default, fixed retrieval pipeline) | "agent" (tool-use loop,
# requires a cloud provider with tool calling; Ollama always falls back to rag)
QA_MODE: str = os.environ.get("QA_MODE", "rag")

# Sidecar API 访问令牌（V-01 修复）：Tauri 宿主每次启动 sidecar 时随机生成，
# 经 AI_READER_SIDECAR_TOKEN 环境变量传入。为空 = 不启用鉴权（web 直跑/开发模式）。
SIDECAR_TOKEN: str = os.environ.get("AI_READER_SIDECAR_TOKEN", "")

# Preserve .env initial values as fallback for mode switching
_ENV_LLM_API_KEY = LLM_API_KEY
_ENV_LLM_BASE_URL = LLM_BASE_URL
_ENV_LLM_MODEL = LLM_MODEL


# VoT (Visualization-of-Thought) spatial reasoning guide injection.
# When True, a spatial reasoning guide is injected into the extraction prompt
# to improve spatial relationship extraction quality.
VOT_SPATIAL_ENABLED: bool = True

# LLM quality review for aggregated entity profiles (Phase 2).
# When True, a single LLM call reviews top entities after aggregation.
LLM_QUALITY_REVIEW: bool = False

# Relation dimension schema v1 (FR-1.2/FR-1.3; docs/analysis/relation-dimension-schema-v1.md).
# When False, the dimension guide is not injected into the extraction prompt and
# dimension sanitization/voting is skipped — prompt and parsing behave exactly
# as before the dimension upgrade (NFR-3).
RELATION_DIMENSIONS_ENABLED: bool = os.environ.get(
    "RELATION_DIMENSIONS_ENABLED", "true"
).strip().lower() not in ("0", "false", "no", "off")

# Self-consistency sample count for rel_subtype voting (FR-1.3). The main
# extraction pass counts as the first sample, so N=3 (default) adds 2
# lightweight dimension-only calls per chapter. 1 disables voting.
RELATION_SUBTYPE_VOTE_SAMPLES: int = int(os.environ.get("RELATION_SUBTYPE_VOTE_SAMPLES", "3"))

# LLM 增量实体消解 (Epic 2, FR-2.1–2.4)。默认开;关闭后聚合期不做
# embedding blocking + LLM 聚类判定,行为与 v0.73 完全一致 (NFR-3)。
ENTITY_RESOLUTION_ENABLED: bool = os.environ.get(
    "ENTITY_RESOLUTION_ENABLED", "true"
).strip().lower() not in ("0", "false", "no", "off")

# Candidate blocking: 余弦相似度阈值 + top-k 近邻成簇,簇外不做 LLM 判定 (FR-2.1)。
ER_SIMILARITY_THRESHOLD: float = float(os.environ.get("ER_SIMILARITY_THRESHOLD", "0.75"))
ER_TOP_K: int = int(os.environ.get("ER_TOP_K", "5"))
# 单簇最大成员数 — 超出则按相似度截断,保证 LLM 调用量与人物数近似线性 (NFR-2)。
ER_MAX_CLUSTER_SIZE: int = int(os.environ.get("ER_MAX_CLUSTER_SIZE", "12"))

# Context window size (tokens). Auto-detected at startup; 8192 = conservative default.
CONTEXT_WINDOW_SIZE: int = 8192


def update_context_window(size: int) -> None:
    """Update CONTEXT_WINDOW_SIZE at runtime (called after detection)."""
    global CONTEXT_WINDOW_SIZE  # noqa: PLW0603
    CONTEXT_WINDOW_SIZE = size


def get_model_name() -> str:
    """Return the active model name based on current provider."""
    if LLM_PROVIDER == "openai":
        return LLM_MODEL or "unknown"
    return OLLAMA_MODEL


def update_cloud_config(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
) -> None:
    """Hot-update cloud LLM config at runtime (no restart needed).

    Falls back to .env initial values when DB-provided values are empty.
    """
    global LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER_FORMAT  # noqa: PLW0603

    LLM_PROVIDER = provider
    LLM_API_KEY = api_key or _ENV_LLM_API_KEY
    LLM_BASE_URL = base_url or _ENV_LLM_BASE_URL
    LLM_MODEL = model or _ENV_LLM_MODEL
    # Detect API format from provider id or base_url
    LLM_PROVIDER_FORMAT = "anthropic" if (
        provider == "anthropic" or "anthropic.com" in (base_url or "")
    ) else "openai"

    _reset_llm_client()


def switch_to_ollama(model: str = "qwen3:8b") -> None:
    """Hot-switch back to local Ollama mode."""
    global LLM_PROVIDER, OLLAMA_MODEL, LLM_PROVIDER_FORMAT  # noqa: PLW0603

    LLM_PROVIDER = "ollama"
    OLLAMA_MODEL = model
    LLM_PROVIDER_FORMAT = "openai"

    _reset_llm_client()


def _reset_llm_client() -> None:
    """Reset cached LLM client and notify AnalysisService singleton."""
    from src.infra import llm_client

    llm_client._client = None

    # Also refresh the AnalysisService singleton so new tasks use the new client
    from src.services.analysis_service import refresh_service_clients

    refresh_service_clients()


def update_max_tokens(max_tokens: int) -> None:
    """Update LLM_MAX_TOKENS at runtime."""
    global LLM_MAX_TOKENS  # noqa: PLW0603

    LLM_MAX_TOKENS = max_tokens


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    GEONAMES_DIR.mkdir(parents=True, exist_ok=True)
