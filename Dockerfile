# Stage 1: build frontend
FROM node:20-alpine AS frontend

WORKDIR /app/console
COPY console/package*.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npx vite build

# Stage 2: Python backend + static frontend
FROM python:3.11-slim

WORKDIR /app

ARG HLAB_TORCH_INSTALL=auto
ARG HLAB_TORCH_CPU_VERSION=2.5.1+cpu
ARG HLAB_TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV HLAB_HF_ENDPOINT=https://hf-mirror.com \
    HF_ENDPOINT=https://hf-mirror.com \
    HLAB_PRELOAD_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps.
# Default "auto" lets PyPI/sentence-transformers resolve the torch build for
# broad cloud compatibility. Use HLAB_TORCH_INSTALL=cpu only for smaller
# CPU-only deployments, or "none" when the base image already provides torch.
COPY pyproject.toml .
RUN if [ "${HLAB_TORCH_INSTALL}" = "cpu" ]; then \
        pip install --no-cache-dir --index-url "${HLAB_TORCH_CPU_INDEX_URL}" "torch==${HLAB_TORCH_CPU_VERSION}"; \
    elif [ "${HLAB_TORCH_INSTALL}" = "none" ]; then \
        echo "Skipping torch preinstall"; \
    else \
        echo "Using default torch resolution from Python dependencies"; \
    fi
RUN pip install --no-cache-dir -e ".[rag]"

# Best-effort embedding warmup. Runtime also exposes /api/v1/vector-admin/warmup,
# so a temporary mirror/network issue must not make the image impossible to build.
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.getenv('HLAB_PRELOAD_EMBEDDING_MODEL', 'BAAI/bge-small-zh-v1.5'))" \
    || echo "WARNING: embedding model preload failed; use Settings > Embedding Model > Warm Up after deployment"

COPY server/ server/
COPY --from=frontend /app/console/dist /app/static

RUN mkdir -p data/vectors data/uploads data/cache

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
