"""Sidecar entry point — PyInstaller-compatible FastAPI launcher.

Usage:
    python sidecar_entry.py --port 12345
    ./ai-reader-backend --port 12345   (after PyInstaller bundling)
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Reader V2 Backend Sidecar")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址")
    args = parser.parse_args()

    # PyInstaller frozen environment support
    if getattr(sys, "frozen", False):
        import multiprocessing

        multiprocessing.freeze_support()

    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
