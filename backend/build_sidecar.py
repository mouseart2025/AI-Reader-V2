"""Build the AI Reader sidecar binary using PyInstaller.

Usage:
    cd backend
    uv run python build_sidecar.py

Output:
    frontend/src-tauri/binaries/ai-reader-sidecar-{target_triple}
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_TAURI = ROOT.parent / "frontend" / "src-tauri"
BIN_DIR = FRONTEND_TAURI / "binaries"


def get_target_triple() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()

    if system == "darwin":
        arch = "aarch64" if machine == "arm64" else "x86_64"
        return f"{arch}-apple-darwin"
    elif system == "windows":
        return "x86_64-pc-windows-msvc"
    elif system == "linux":
        arch = "x86_64" if machine in ("x86_64", "amd64") else machine
        return f"{arch}-unknown-linux-gnu"
    else:
        raise RuntimeError(f"Unsupported platform: {system} {machine}")


def main() -> None:
    target = get_target_triple()
    bin_name = f"ai-reader-sidecar-{target}"
    if platform.system() == "Windows":
        bin_name += ".exe"

    print(f"Building sidecar for {target}...")

    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "ai-reader-sidecar",
        "--noconfirm",
        "--clean",
        # Include the backend source package
        "--paths", str(ROOT),
        # Collect all submodules in the src package (routes, services, etc.)
        "--collect-submodules", "src",
        # Include data files needed at runtime
        "--add-data", f"{ROOT / 'data'}:data",
        # Include extraction prompts
        "--add-data", f"{ROOT / 'src' / 'extraction' / 'prompts'}:src/extraction/prompts",
        # Hidden imports that PyInstaller may miss
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "jieba",
        "--hidden-import", "httpx",
        "--hidden-import", "multipart",
        "--hidden-import", "multipart.multipart",
        "--hidden-import", "python_multipart",
        "--hidden-import", "python_multipart.multipart",
        str(ROOT / "sidecar_entry.py"),
    ]

    subprocess.run(cmd, check=True, cwd=str(ROOT))

    # Move binary to Tauri binaries dir with target triple suffix
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    src_ext = ".exe" if platform.system() == "Windows" else ""
    src = ROOT / "dist" / f"ai-reader-sidecar{src_ext}"
    dst = BIN_DIR / bin_name

    shutil.copy2(str(src), str(dst))
    print(f"Sidecar binary: {dst}")


if __name__ == "__main__":
    main()
