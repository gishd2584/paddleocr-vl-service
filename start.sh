#!/usr/bin/env bash
# PaddleOCR-VL 文档解析服务 启动脚本
set -e

# ---- 可配置环境变量 ----
export PADDLEOCR_DEVICE="${PADDLEOCR_DEVICE:-gpu:1}"        # 指定 GPU 卡，避开高占用卡
export PADDLEOCR_PIPELINE_VERSION="${PADDLEOCR_PIPELINE_VERSION:-}"  # 留空=默认; 可选 v1.5 / v1.6
export PADDLEOCR_USE_ORIENT="${PADDLEOCR_USE_ORIENT:-False}"   # 文档方向分类
export PADDLEOCR_USE_UNWARP="${PADDLEOCR_USE_UNWARP:-False}"   # 文本图像矫正
export PADDLEOCR_USE_LAYOUT="${PADDLEOCR_USE_LAYOUT:-True}"    # 版面区域检测排序
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"

echo ">>> 启动 PaddleOCR-VL 文档解析服务"
echo ">>> device=${PADDLEOCR_DEVICE}  port=${PORT}  version=${PADDLEOCR_PIPELINE_VERSION:-default}"

# 仅 1 个 worker：模型常驻显存，且推理加锁避免并发占卡
exec python -m uvicorn server:app --host "$HOST" --port "$PORT" --workers 1
