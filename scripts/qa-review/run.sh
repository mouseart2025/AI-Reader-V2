#!/bin/bash
# Q0 质量数据核对工具 — 一键启动。
# 用法: ./run.sh   （或用 QA_DATA_DIR 覆盖数据目录 / QA_PORT 覆盖端口）
cd "$(dirname "$0")"
echo "启动 Q0 数据核对工具..."
python3 serve.py
