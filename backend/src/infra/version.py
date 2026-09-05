"""后端版本号唯一来源 — 由 scripts/bump-version.sh 同步。

桌面端 sidecar 经 PyInstaller 打包,运行时读不到 pyproject.toml /
importlib.metadata,因此版本号以常量内置,经 /api/health 暴露给 Tauri 宿主
做前后端版本一致性校验(issue #71:升级残留旧 sidecar 时新端点 405)。
"""

BACKEND_VERSION = "0.76.0-beta.6"
