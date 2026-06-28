#!/bin/bash
# RHGPT 本地项目一键启动脚本

echo "=================================================="
echo "🚀 正在启动 RHGPT 多模型接力系统 (Backend + Frontend)..."
echo "=================================================="

# 启动 Python 后端 (端口 8000)
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 启动 Next.js 前端 (端口 3000)
cd frontend && npm run dev &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID" EXIT

wait
