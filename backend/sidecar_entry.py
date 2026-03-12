"""Sidecar entry point — PyInstaller-compatible FastAPI launcher.

Usage:
    python sidecar_entry.py --port 12345
    ./ai-reader-sidecar --port 12345   (after PyInstaller bundling)
"""

import sys
import multiprocessing

# CRITICAL: freeze_support() must be called at module level before any other
# imports on Windows, otherwise PyInstaller child processes crash immediately.
if getattr(sys, "frozen", False):
    multiprocessing.freeze_support()

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Reader V2 Backend Sidecar")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址")
    args = parser.parse_args()

    # Signal the port to the parent process (Tauri reads this line from stdout)
    print(f"PORT:{args.port}", flush=True)

    try:
        import uvicorn
        from src.api.main import app

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except Exception as e:
        # Ensure crash info reaches Tauri's stderr reader for diagnostics
        print(f"FATAL: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
