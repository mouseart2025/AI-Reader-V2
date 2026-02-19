# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI Reader V2 backend sidecar.

Build:
    cd backend
    uv pip install pyinstaller
    pyinstaller ai-reader-backend.spec

Output lands in dist/ai-reader-backend (single file).
Copy to src-tauri/binaries/ with target-triple suffix — handled by scripts/build-sidecar.sh.
"""

import importlib
from pathlib import Path

backend_dir = Path(SPECPATH)

# ── Locate jieba dict data ───────────────────────
jieba_pkg = Path(importlib.import_module("jieba").__file__).parent
jieba_datas = [
    (str(jieba_pkg / "dict.txt"), "jieba"),
    (str(jieba_pkg / "finalseg"), "jieba/finalseg"),
    (str(jieba_pkg / "posseg"), "jieba/posseg"),
    (str(jieba_pkg / "analyse"), "jieba/analyse"),
    (str(jieba_pkg / "lac_small"), "jieba/lac_small"),
]

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=[
        # Application source
        ("src", "src"),
        # jieba dictionaries
        *jieba_datas,
    ],
    hiddenimports=[
        # ── uvicorn internals ──
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # ── FastAPI + Starlette ──
        "fastapi",
        "fastapi.responses",
        "fastapi.middleware",
        "starlette",
        "starlette.responses",
        "starlette.websockets",
        "starlette.middleware.cors",
        # ── Pydantic ──
        "pydantic",
        "pydantic.deprecated.decorator",
        # ── Async DB ──
        "aiosqlite",
        "sqlite3",
        # ── HTTP client ──
        "httpx",
        "httpx._transports",
        # ── WebSocket ──
        "websockets",
        # ── NLP ──
        "jieba",
        "jieba.finalseg",
        "jieba.posseg",
        "jieba.analyse",
        # ── Embeddings / Vector DB ──
        "chromadb",
        "sentence_transformers",
        # ── Multipart upload ──
        "multipart",
        "python_multipart",
        # ── Image (map tiles) ──
        "PIL",
        # ── Perlin noise (map layout) ──
        "opensimplex",
        # ── Secure credential storage ──
        "keyring",
        "keyring.backends",
        # ── Stdlib extras sometimes missed ──
        "email.mime.text",
        "email.mime.multipart",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Large unused packages
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy.testing",
        "IPython",
        "notebook",
        "pytest",
        "setuptools",
        "pip",
        "wheel",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ai-reader-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
)
