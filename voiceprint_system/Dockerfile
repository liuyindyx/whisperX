# ==============================================================================
# 🎙️ WhisperX Voiceprint System - Dockerfile
# ==============================================================================

FROM docker.m.daocloud.io/library/python:3.10-slim-bookworm

# 1. 安装系统依赖：ffmpeg (关键)、git、build-essential (有些依赖编译需要)、libsndfile1 (人声音频库依赖)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 2. 从官方镜像拷贝 astral uv 工具，大幅提升 python 包安装速度
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 3. 设置工作目录与模型缓存环境变量 (用于 K8s/Docker PVC 挂载，防止重启重复下载)
WORKDIR /app
ENV HF_HOME=/app/cache/huggingface
ENV TORCH_HOME=/app/cache/torch
ENV SPEECHBRAIN_CACHE=/app/cache/speechbrain

# 4. 拷贝依赖清单并优先安装，以充分利用 Docker 缓存层
COPY pyproject.toml uv.lock ./
COPY voiceprint_system/requirements.txt ./voiceprint_system/requirements.txt

# 使用 uv 加速全局系统包安装
RUN uv pip install --system --no-cache-dir -r voiceprint_system/requirements.txt
RUN uv pip install --system --no-cache-dir -e .

# 5. 拷贝项目所有源码
COPY . .

# 创建必要的挂载点目录
RUN mkdir -p meetings voiceprint_system/database cache

# 6. 暴露服务端端口
EXPOSE 8000

# 7. 启动 FastAPI API 网站服务器
CMD ["uvicorn", "voiceprint_system.server:app", "--host", "0.0.0.0", "--port", "8000"]
