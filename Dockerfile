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

ARG AEZAB_TORCH_INSTALL=
ARG AEZAB_TORCH_CPU_VERSION=
ARG AEZAB_TORCH_CPU_INDEX_URL=
ARG HLAB_TORCH_INSTALL=
ARG HLAB_TORCH_CPU_VERSION=
ARG HLAB_TORCH_CPU_INDEX_URL=

ENV HF_ENDPOINT=https://hf-mirror.com

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps.
# Default "auto" lets PyPI/sentence-transformers resolve the torch build for
# broad cloud compatibility. Use AEZAB_TORCH_INSTALL=cpu only for smaller
# CPU-only deployments, or "none" when the base image already provides torch.
COPY pyproject.toml .
RUN TORCH_INSTALL="${AEZAB_TORCH_INSTALL:-${HLAB_TORCH_INSTALL:-auto}}"; \
    TORCH_CPU_VERSION="${AEZAB_TORCH_CPU_VERSION:-${HLAB_TORCH_CPU_VERSION:-2.5.1+cpu}}"; \
    TORCH_CPU_INDEX_URL="${AEZAB_TORCH_CPU_INDEX_URL:-${HLAB_TORCH_CPU_INDEX_URL:-https://download.pytorch.org/whl/cpu}}"; \
    if [ "${TORCH_INSTALL}" = "cpu" ]; then \
        pip install --no-cache-dir --index-url "${TORCH_CPU_INDEX_URL}" "torch==${TORCH_CPU_VERSION}"; \
    elif [ "${TORCH_INSTALL}" = "none" ]; then \
        echo "Skipping torch preinstall"; \
    else \
        echo "Using default torch resolution from Python dependencies"; \
    fi
RUN pip install --no-cache-dir -e ".[rag]"

# The embedding model is NOT pre-downloaded at build time (it used to warm
# the wrong default here and still left the real model, BAAI/bge-m3, to
# download silently on first upload). Instead, the console's Knowledge page
# shows the model's download status and lets the user trigger it explicitly
# via POST /api/v1/vector-admin/warmup, which downloads/loads it in the
# background.

COPY server/ server/
COPY --from=frontend /app/console/dist /app/static

RUN mkdir -p data/vectors data/uploads data/cache

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
