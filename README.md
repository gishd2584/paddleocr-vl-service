# PaddleOCR-VL 文档解析服务

基于 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 的 `PaddleOCRVL` 文档解析流水线，
提供 Web 上传界面：上传图片 / PDF，自动解析为结构化 **Markdown**（含表格、公式、图表、印章等），
并在线预览 / 下载。界面风格参考 [MinerU Extractor](https://mineru.net/OpenSourceTools/Extractor)。

## 功能

- 上传 **图片**（PNG/JPG/WEBP/BMP）或 **PDF**，一键解析为 Markdown
- 三栏结果视图：**解析预览**（Markdown 渲染，支持 LaTeX 公式）/ **Markdown 源码** / **JSON**
- 原文预览（图片直接显示；PDF 内嵌预览）
- 一键下载 Markdown / JSON / 全部产物
- 可配置：GPU 设备、模型版本（v1.5 / v1.6）、版面检测、文档方向分类、图像矫正
- 推理加锁，避免多请求并发占满显存
- **任务 + 轮询架构**：上传立即返回 `job_id`，前端轮询状态，避免首次模型下载（数 GB）把长连接卡死（尤其经 VSCode / 代理端口转发时）

## 部署（在你的 Linux GPU 服务器上）

### 1. 安装依赖

```bash
# 1) 文档解析依赖（含 paddleocr + PaddleOCR-VL 模型，需 GPU 环境）
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -m pip install -U "paddleocr[doc-parser]>=3.6.0"

# 2) 服务依赖
python -m pip install -r requirements.txt
```

> 首次解析会自动下载模型权重（约数 GB），请保证磁盘与网络可用。

### 2. 启动

```bash
chmod +x start.sh
./start.sh
# 或自定义设备/端口
PADDLEOCR_DEVICE=gpu:1 PORT=8000 ./start.sh
```

打开浏览器访问 `http://<服务器IP>:8000`。

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `PADDLEOCR_DEVICE` | `gpu:1` | 推理设备，如 `gpu:0` / `gpu:1` / `cpu` |
| `PADDLEOCR_PIPELINE_VERSION` | 空 | 模型版本，`v1.5` / `v1.6` |
| `PADDLEOCR_USE_ORIENT` | `False` | 文档方向分类 |
| `PADDLEOCR_USE_UNWARP` | `False` | 文本图像矫正 |
| `PADDLEOCR_USE_LAYOUT` | `True` | 版面区域检测排序 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址与端口 |

## API

- `GET  /api/health` — 健康检查（返回 device、模型是否已加载、当前排队任务数）
- `POST /api/parse` — 上传文件，**立即返回** `{ job_id, input_url, status:"pending" }`；真正的下载+推理在后台执行
  - 表单字段：`file`
  - 查询参数：`device`、`pipeline_version`、`use_orient`、`use_unwarp`、`use_layout`
- `GET  /api/status/{job_id}` — 轮询状态：`{ status:"pending|running|done|error", message, result|error }`
  - `result` 结构：`{ job_id, filename, markdown, markdown_pages, json_pages, images, files, results_base, input_url }`
- `POST /api/warmup` — 预加载模型（提前触发权重下载，不解析文件）
- `GET  /results/{job_id}/...` — 访问解析产物（图片 / md / json）
- `GET  /` — 前端页面

## 排错

- **一直卡在「解析中」**：多半是首次运行正在下载模型权重（数 GB）。现在已改为轮询，
  进度会实时显示在遮罩上；**看服务器终端的 stdout**（PaddleOCR 会打印下载进度/报错）。
  若仍长时间无进展，多半是下载源网络慢或失败，终端会有 `ModuleNotFoundError` / 下载异常等 traceback。
- **VSCode 端口转发卡死**：旧版把下载塞进一条长请求，易被代理掐断；v1.1 起用轮询规避。
  若转发仍不稳定，可临时用 `ssh -L 8000:127.0.0.1:8000 user@server` 直连，或加一层 Nginx 反代。
- **GPU 显存不足**：调小并发（本服务已串行加锁），或用 `PADDLEOCR_DEVICE=gpu:0` 换卡。

## 目录结构

```
paddleocr-vl-service/
├── server.py          # FastAPI 后端
├── requirements.txt
├── start.sh           # 启动脚本
├── static/
│   ├── index.html     # 前端页面
│   ├── style.css
│   └── app.js
└── results/           # 解析产物（运行时自动生成）
```

## 与你的示例代码的关系

本服务等价于把下面这段逻辑封装成了 Web 接口，并在其基础上支持 PDF、批量产物与在线预览：

```python
from paddleocr import PaddleOCRVL

pipeline = PaddleOCRVL(device="gpu:1")
output = pipeline.predict("/path/to/image.png")
for res in output:
    res.print()
    res.save_to_markdown(save_path="./output1")
```
