# Stage 1: build frontend
FROM node:20-alpine AS frontend

WORKDIR /app/console
COPY console/package*.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

# Stage 2: Python backend + static frontend
FROM python:3.11-slim

WORKDIR /app

ARG AEZAB_TORCH_INSTALL=
ARG AEZAB_TORCH_CPU_VERSION=
ARG AEZAB_TORCH_CPU_INDEX_URL=
ARG HLAB_TORCH_INSTALL=
ARG HLAB_TORCH_CPU_VERSION=
ARG HLAB_TORCH_CPU_INDEX_URL=

ENV HF_ENDPOINT=https://hf-mirror.com \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/aezab/.cache/huggingface

# Python deps.
# CPU embeddings are the default for a small self-hosted server. Installing
# unqualified torch on Linux pulls CUDA libraries even on a machine without
# a GPU. GPU operators can explicitly select auto or a different wheel index.
# The supported Python 3.11 Linux dependencies provide wheels; no compiler
# toolchain or Debian package download is needed in the runtime image.
COPY pyproject.toml LICENSE ./
RUN TORCH_INSTALL="${AEZAB_TORCH_INSTALL:-${HLAB_TORCH_INSTALL:-cpu}}"; \
    TORCH_CPU_VERSION="${AEZAB_TORCH_CPU_VERSION:-${HLAB_TORCH_CPU_VERSION:-2.9.1+cpu}}"; \
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
COPY alembic/ alembic/
COPY alembic.ini .
COPY --from=frontend /app/console/dist /app/static
COPY static/widget.js static/widget.js

RUN groupadd --gid 10001 aezab \
    && useradd --uid 10001 --gid aezab --create-home aezab \
    && mkdir -p data/vectors data/uploads data/cache /home/aezab/.cache/huggingface \
    && chown -R aezab:aezab /app/data /home/aezab

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json, urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)); assert r.get('status') == 'ok'"

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
