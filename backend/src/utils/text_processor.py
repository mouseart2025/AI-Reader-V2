"""Text encoding detection utilities."""


def detect_encoding(raw: bytes) -> str:
    """Detect text encoding by attempting decode in priority order.

    Tries UTF-8 first, then GB18030 (which is a superset of GBK/GB2312).
    Falls back to UTF-8 with replacement characters if both fail.

    Only examines the first 100KB for performance on large files.
    """
    sample = raw[:102400]  # 100KB

    # Try UTF-8 first
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # Try GB18030 (superset of GBK and GB2312)
    try:
        sample.decode("gb18030")
        return "gb18030"
    except UnicodeDecodeError:
        pass

    # Fallback — will use errors="replace" at call site
    return "utf-8"


def decode_text(raw: bytes) -> str:
    """Decode raw bytes to string using detected encoding."""
    encoding = detect_encoding(raw)
    if encoding == "utf-8":
        # Try strict first, fall back to replace
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    return raw.decode(encoding)
