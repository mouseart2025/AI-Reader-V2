#!/usr/bin/env python3
"""Compile prompt files into an obfuscated Python module.

Used during sidecar build to embed prompts into the binary,
making them harder to extract than plain text files.

Usage:
    python scripts/compile_prompts.py

Output:
    src/extraction/_compiled_prompts.py
"""

import base64
import zlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "src" / "extraction" / "prompts"
OUTPUT = Path(__file__).parent.parent / "src" / "extraction" / "_compiled_prompts.py"


def compile_prompts():
    entries: dict[str, str] = {}
    for f in sorted(PROMPTS_DIR.iterdir()):
        if f.suffix in (".txt", ".json"):
            content = f.read_text(encoding="utf-8")
            entries[f.stem] = content
            print(f"  {f.name}: {len(content):,} chars")

    # Build Python module with compressed+encoded prompts
    lines = [
        '"""Compiled prompts — auto-generated, do not edit."""',
        "",
        "import base64",
        "import zlib",
        "",
        "",
        "def _d(s: str) -> str:",
        '    """Decode compressed prompt."""',
        "    return zlib.decompress(base64.b85decode(s)).decode('utf-8')",
        "",
        "",
        "PROMPTS = {",
    ]

    for name, content in entries.items():
        compressed = base64.b85encode(zlib.compress(content.encode("utf-8"), 9))
        encoded = compressed.decode("ascii")
        # Split long strings into chunks for readability
        chunks = [encoded[i:i + 100] for i in range(0, len(encoded), 100)]
        if len(chunks) == 1:
            lines.append(f'    "{name}": _d("{chunks[0]}"),')
        else:
            lines.append(f'    "{name}": _d(')
            for chunk in chunks:
                lines.append(f'        "{chunk}"')
            lines.append("    ),")

    lines.append("}")
    lines.append("")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nCompiled {len(entries)} prompts → {OUTPUT}")
    print(f"Output size: {OUTPUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    compile_prompts()
