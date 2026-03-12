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
import os
from pathlib import Path


def _crash_log_path() -> Path:
    """Return a writable crash log path next to the executable or in temp."""
    if getattr(sys, "frozen", False):
        # Try user's home data dir first
        data_dir = Path.home() / ".ai-reader-v2"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir / "sidecar-crash.log"
        except Exception:
            pass
    import tempfile
    return Path(tempfile.gettempdir()) / "ai-reader-sidecar-crash.log"


def main() -> None:
    # Write early diagnostic marker so we know Python started
    crash_log = _crash_log_path()
    try:
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(f"sidecar starting: python={sys.version}, frozen={getattr(sys, 'frozen', False)}\n")
            f.write(f"executable={sys.executable}\n")
            f.write(f"argv={sys.argv}\n")
            f.write(f"cwd={os.getcwd()}\n")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AI Reader V2 Backend Sidecar")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址")
    args = parser.parse_args()

    # Signal the port to the parent process (Tauri reads this line from stdout)
    print(f"PORT:{args.port}", flush=True)

    try:
        import uvicorn
        from src.api.main import app

        # Clear crash log on successful startup
        try:
            crash_log.unlink(missing_ok=True)
        except Exception:
            pass

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except Exception as e:
        # Write crash info to both stderr and log file
        error_msg = f"FATAL: {e}"
        print(error_msg, file=sys.stderr, flush=True)
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr, flush=True)
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n{error_msg}\n{tb}\n")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
