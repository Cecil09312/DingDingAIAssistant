# 钉钉AI智能体助手 Docker 镜像（CPU 精简版）
# 构建：docker compose build
# 说明：构建期预下载全部模型（Embedding/Reranker/OCR）到镜像内，
#       交付后冷启动无需联网下载模型。
# 基础镜像走 DaoCloud 国内镜像加速拉取（Docker Hub 镜像代理）
FROM docker.m.daocloud.io/library/python:3.12-slim

# 替换 apt 为阿里云 Debian 源加速（Bookworm 使用 DEB822 格式 sources 文件）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# easyocr/torch cv2 运行依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖安装（先拷 requirements.txt 利用层缓存）
COPY requirements.txt .
# pip 默认走清华镜像加速；torch + torchvision 单独用官方 CPU 轮子源（体积小，不含 CUDA）
# torchvision 必装：新版 transformers 导入 AutoProcessor 时会间接 import torchvision，
# 缺失会报 "Could not import module 'AutoProcessor'" 导致 prefetch 失败
# 加大超时与重试次数，避免国内网络访问 PyPI 官方源（files.pythonhosted.org）时读超时
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# 拷贝项目代码（.dockerignore 已排除 data/ 与 .env）
COPY . .

# 构建期预下载模型：HF 缓存固定到镜像内路径，使用 hf-mirror 加速
ENV HF_HOME=/app/.hf_cache \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_SYMLINKS_WARNING=true
RUN python scripts/prefetch_models.py

# 运行时强制 CPU + 离线模式（模型已在构建期预下载，运行时无需联网）
ENV EMBEDDING_DEVICE=cpu \
    OCR_USE_GPU=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8000

# 单 worker：短期记忆为进程内 MemorySaver，禁止多进程
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
