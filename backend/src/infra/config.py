import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("AI_READER_DATA_DIR", Path.home() / ".ai-reader-v2"))
DB_PATH = DATA_DIR / "data.db"
CHROMA_DIR = DATA_DIR / "chroma"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")

# LLM Provider: "ollama" (default, local) or "openai" (cloud, OpenAI-compatible)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

# Cloud LLM settings (used when LLM_PROVIDER="openai")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8192"))


def get_model_name() -> str:
    """Return the active model name based on current provider."""
    if LLM_PROVIDER == "openai":
        return LLM_MODEL or "unknown"
    return OLLAMA_MODEL


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
