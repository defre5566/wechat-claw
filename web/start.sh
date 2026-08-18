#!/usr/bin/env bash
# wechat-claw web 启动脚本（Linux/macOS）
# 用法: ./web/start.sh [--port 8650]
set -e
cd "$(dirname "$0")/.."
exec python3 web/launcher.py "$@"
