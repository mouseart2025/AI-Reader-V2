"""Text encoding detection utilities."""


def detect_encoding(raw: bytes) -> str:
    """Detect text encoding by attempting decode in priority order.

    Tries UTF-8 first, then GB18030 (which is a superset of GBK/GB2312).
    Uses a sample that's trimmed to avoid splitting multi-byte characters.
    """
    # Use a sample for speed, but trim to avoid multi-byte boundary issues.
    # GB18030 uses up to 4 bytes per character, so trim up to 3 bytes.
    sample = raw[:102400]

    # Try UTF-8 first (strict)
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # Try GB18030 — trim 0-3 bytes from end to handle boundary splits
    for trim in range(4):
        s = sample[: len(sample) - trim] if trim else sample
        if not s:
            continue
        try:
            s.decode("gb18030")
            return "gb18030"
        except UnicodeDecodeError:
            continue

    # Fallback — will use errors="replace" at call site
    return "utf-8"


def decode_text(raw: bytes) -> str:
    """Decode raw bytes to string using detected encoding."""
    encoding = detect_encoding(raw)

    if encoding == "utf-8":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            # UTF-8 failed on full content — try GB18030 before giving up
            try:
                return raw.decode("gb18030")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")

    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode(encoding, errors="replace")
