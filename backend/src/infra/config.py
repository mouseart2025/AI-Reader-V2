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

# LLM Provider: "ollama" (default), "openai" (OpenAI-compatible), or "codex"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

# Codex CLI settings. ``LLM_PROVIDER=codex`` reuses the CLI's saved
# authentication. An empty model keeps Codex's current default instead of
# pinning a model that may not be available to every account.
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "")
CODEX_REASONING_EFFORT = os.environ.get("CODEX_REASONING_EFFORT", "low")
CODEX_MIN_TIMEOUT_SECONDS = int(os.environ.get("CODEX_MIN_TIMEOUT_SECONDS", "600"))
CODEX_CONTEXT_WINDOW = int(os.environ.get("CODEX_CONTEXT_WINDOW", "131072"))
CODEX_EXAMPLE_COUNT = max(0, int(os.environ.get("CODEX_EXAMPLE_COUNT", "1")))
CODEX_MAX_BATCH_CHAPTERS = max(
    0, int(os.environ.get("CODEX_MAX_BATCH_CHAPTERS", "10"))
)

# Optional LLM passes can be disabled for quota-sensitive bulk extraction.
# Core per-chapter fact extraction remains enabled.
SCENE_LLM_ENABLED = os.environ.get("SCENE_LLM_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}
AUXILIARY_LLM_ENABLED = os.environ.get("AUXILIARY_LLM_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}

# Cloud LLM settings (used when LLM_PROVIDER="openai")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))

# API protocol format for cloud providers: "openai" (default) | "anthropic"
# Separate from LLM_PROVIDER so users can use Anthropic-compatible proxies
LLM_PROVIDER_FORMAT: str = "openai"

# Preserve .env initial values as fallback for mode switching
_ENV_LLM_API_KEY = LLM_API_KEY
_ENV_LLM_BASE_URL = LLM_BASE_URL
_ENV_LLM_MODEL = LLM_MODEL


# VoT (Visualization-of-Thought) spatial reasoning guide injection.
# When True, a spatial reasoning guide is injected into the extraction prompt
# to improve spatial relationship extraction quality.
VOT_SPATIAL_ENABLED: bool = os.environ.get(
    "VOT_SPATIAL_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}

# LLM quality review for aggregated entity profiles (Phase 2).
# When True, a single LLM call reviews top entities after aggregation.
LLM_QUALITY_REVIEW: bool = False

# Context window size (tokens). Auto-detected at startup; 8192 = conservative default.
CONTEXT_WINDOW_SIZE: int = 8192


def update_context_window(size: int) -> None:
    """Update CONTEXT_WINDOW_SIZE at runtime (called after detection)."""
    global CONTEXT_WINDOW_SIZE  # noqa: PLW0603
    CONTEXT_WINDOW_SIZE = size


def get_model_name() -> str:
    """Return the active model name based on current provider."""
    if LLM_PROVIDER == "codex":
        return CODEX_MODEL or "codex-default"
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
