"""Source-language helpers for import and analysis pipelines."""

from typing import Literal

SourceLanguage = Literal["auto", "zh-CN", "vi", "en"]

SUPPORTED_SOURCE_LANGUAGES: tuple[SourceLanguage, ...] = ("auto", "zh-CN", "vi", "en")
DEFAULT_SOURCE_LANGUAGE: SourceLanguage = "zh-CN"

_ALIASES: dict[str, SourceLanguage] = {
    "auto": "auto",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh_cn": "zh-CN",
    "chinese": "zh-CN",
    "cn": "zh-CN",
    "vi": "vi",
    "vn": "vi",
    "vietnamese": "vi",
    "en": "en",
    "english": "en",
}


def normalize_source_language(value: str | None) -> SourceLanguage:
    """Normalize external source-language input to a supported ID."""
    if not value:
        return DEFAULT_SOURCE_LANGUAGE
    normalized = value.strip().lower()
    return _ALIASES.get(normalized, DEFAULT_SOURCE_LANGUAGE)

