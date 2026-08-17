"""
PaddleOCR-VL 文档解析服务
========================
基于 PaddleOCR 的 PaddleOCRVL 文档解析流水线，提供 Web 上传解析接口。

架构说明（v1.1）:
    上传接口 `/api/parse` 立即返回 job_id，真正的「模型下载 + 推理」在后台任务中
    执行；前端通过 `/api/status/{job_id}` 轮询状态。这样可以避免把数 GB 的首次
    模型下载塞进一条会被代理/网关掐断的长连接里。

启动:
    export PADDLEOCR_DEVICE=gpu:1      # 指定的 GPU 卡，避开高占用卡
    python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

注意:
    - 仅使用 1 个 worker（模型常驻显存，且推理加锁避免并发占卡）。
    - PaddleOCRVL 在首次解析时会自动下载模型权重（可能数 GB，需耐心等待）。
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("paddleocr-vl")

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = BASE_DIR / "static"

# ----- 服务端默认配置（可被环境变量覆盖，也可被请求参数覆盖）-----
DEVICE = os.getenv("PADDLEOCR_DEVICE", "gpu:1")
PIPELINE_VERSION = os.getenv("PADDLEOCR_PIPELINE_VERSION", "")  # 留空=使用 paddleocr 默认版本
DEFAULT_ORIENT = os.getenv("PADDLEOCR_USE_ORIENT", "False").lower() == "true"
DEFAULT_UNWARP = os.getenv("PADDLEOCR_USE_UNWARP", "False").lower() == "true"
DEFAULT_LAYOUT = os.getenv("PADDLEOCR_USE_LAYOUT", "True").lower() != "false"

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"}

app = FastAPI(title="PaddleOCR-VL 文档解析服务", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 推理锁：同一时刻只跑一条流水线，保护单卡显存
_infer_lock = asyncio.Lock()
# 流水线缓存：相同配置复用同一个已加载的模型
_pipeline_cache: dict = {}
# 任务表：job_id -> {"status", "message", "result"/"error"}
JOBS: dict = {}


def _build_pipeline(device, pipeline_version, use_orient, use_unwarp, use_layout):
    """按需构建（并下载）PaddleOCRVL 流水线。仅在使用时才 import paddleocr。"""
    from paddleocr import PaddleOCRVL

    kwargs = dict(
        device=device,
        use_doc_orientation_classify=use_orient,
        use_doc_unwarping=use_unwarp,
        use_layout_detection=use_layout,
    )
    if pipeline_version:
        kwargs["pipeline_version"] = pipeline_version
    return PaddleOCRVL(**kwargs)


def get_pipeline(device, pipeline_version, use_orient, use_unwarp, use_layout):
    key = (device, pipeline_version, use_orient, use_unwarp, use_layout)
    if key not in _pipeline_cache:
        _pipeline_cache[key] = _build_pipeline(*key)
    return _pipeline_cache[key]


def _run_pipeline(raw_path: Path, job_dir: Path, filename: str,
                 device, pipeline_version, use_orient, use_unwarp, use_layout):
    """同步执行解析（在线程池中调用）。"""
    pipeline = get_pipeline(device, pipeline_version, use_orient, use_unwarp, use_layout)
    outputs = pipeline.predict(str(raw_path))

    pages_md, pages_json = [], []
    for idx, res in enumerate(outputs):
        try:
            res.save_to_markdown(save_path=str(job_dir))
        except Exception as e:  # 容错：某一页保存失败不影响其它页
            pages_md.append(f"<!-- 第 {idx + 1} 页 Markdown 保存失败: {e} -->")
        try:
            res.save_to_json(save_path=str(job_dir))
        except Exception as e:
            pages_json.append(f"/* 第 {idx + 1} 页 JSON 保存失败: {e} */")

    # 读回保存的产物
    for p in sorted(job_dir.rglob("*.md")):
        if p.name == "input.md":
            continue
        pages_md.append(p.read_text(encoding="utf-8", errors="ignore"))
    for p in sorted(job_dir.rglob("*.json")):
        pages_json.append(p.read_text(encoding="utf-8", errors="ignore"))

    combined_md = "\n\n---\n\n".join(pages_md)

    image_files = sorted(
        str(p.relative_to(job_dir)).replace("\\", "/")
        for p in job_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    )
    all_files = sorted(
        str(p.relative_to(job_dir)).replace("\\", "/")
        for p in job_dir.rglob("*") if p.is_file()
    )

    return {
        "job_id": job_dir.name,
        "filename": filename,
        "markdown": combined_md,
        "pages": len(pages_md),
        "markdown_pages": pages_md,
        "json_pages": pages_json,
        "images": image_files,
        "files": all_files,
        "results_base": f"/results/{job_dir.name}",
    }


async def _run_job(job_id: str, raw_path: Path, job_dir: Path, filename: str, opts: dict):
    """后台任务：执行模型下载 + 推理，更新 JOBS 状态。"""
    loop = asyncio.get_event_loop()
    try:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["message"] = "模型加载中（首次运行会下载权重，请稍候）…"
        async with _infer_lock:
            result = await loop.run_in_executor(
                None, _run_pipeline, raw_path, job_dir, filename,
                opts["device"], opts["pipeline_version"], opts["use_orient"],
                opts["use_unwarp"], opts["use_layout"],
            )
        result["input_url"] = f"/results/{job_id}/input{raw_path.suffix}"
        JOBS[job_id] = {"status": "done", "message": "解析完成", "result": result}
        logger.info("job %s 完成", job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("job %s 失败", job_id)
        JOBS[job_id] = {"status": "error", "message": f"解析失败: {e}", "error": str(e)}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "pipeline_version": PIPELINE_VERSION or "default",
        "pipeline_loaded": len(_pipeline_cache) > 0,
        "active_jobs": sum(1 for j in JOBS.values() if j["status"] in ("pending", "running")),
    }


@app.post("/api/parse")
async def parse(
    file: UploadFile = File(...),
    device: str = Query(DEVICE),
    pipeline_version: str = Query(PIPELINE_VERSION),
    use_orient: bool = Query(DEFAULT_ORIENT),
    use_unwarp: bool = Query(DEFAULT_UNWARP),
    use_layout: bool = Query(DEFAULT_LAYOUT),
):
    ext = Path(file.filename or "x.bin").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，仅支持 {sorted(ALLOWED_EXT)}")

    job_id = uuid.uuid4().hex[:12]
    job_dir = RESULTS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_path = job_dir / f"input{ext}"
    raw_path.write_bytes(await file.read())

    opts = dict(
        device=device, pipeline_version=pipeline_version,
        use_orient=use_orient, use_unwarp=use_unwarp, use_layout=use_layout,
    )
    JOBS[job_id] = {"status": "pending", "message": "任务已加入队列，等待推理…"}
    asyncio.create_task(_run_job(job_id, raw_path, job_dir, file.filename or f"input{ext}", opts))
    logger.info("job %s 已创建: %s", job_id, file.filename)
    return JSONResponse({
        "job_id": job_id,
        "input_url": f"/results/{job_id}/input{ext}",
        "status": "pending",
    })


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


@app.post("/api/warmup")
async def warmup(
    device: str = Query(DEVICE),
    pipeline_version: str = Query(PIPELINE_VERSION),
    use_orient: bool = Query(DEFAULT_ORIENT),
    use_unwarp: bool = Query(DEFAULT_UNWARP),
    use_layout: bool = Query(DEFAULT_LAYOUT),
):
    """预加载模型（不解析文件），用于提前触发权重下载，避免首次上传等待过久。"""
    try:
        get_pipeline(device, pipeline_version, use_orient, use_unwarp, use_layout)
        return {"status": "ok", "message": "模型已加载"}
    except Exception as e:  # noqa: BLE001
        logger.exception("warmup 失败")
        raise HTTPException(status_code=500, detail=f"模型加载失败: {e}")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico", status_code=204)
def favicon():
    return Response(status_code=204)


# 静态资源
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# 解析结果（markdown 引用的图片、json、md 文件）
app.mount("/results", StaticFiles(directory=str(RESULTS_DIR)), name="results")
