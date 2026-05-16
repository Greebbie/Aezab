"""Global configuration via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "HlAB Agent Builder"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/hlab.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    llm_provider: Literal["openai_compatible", "dashscope", "zhipu", "local"] = "openai_compatible"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-flash"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_timeout: int = 60  # Per-request LLM call timeout in seconds

    # Embedding
    embedding_provider: Literal["local", "dashscope", "openai_compatible"] = "local"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    hf_endpoint: str = "https://hf-mirror.com"

    # Vector Store
    vector_store: Literal["faiss", "pgvector", "milvus", "auto"] = "auto"
    faiss_index_dir: str = "./data/vectors"
    faiss_index_path: str = "./data/vectors/faiss.index"
    milvus_uri: str = "localhost:19530"

    # CORS
    cors_origins: str = "*"

    # Auth
    disable_auth: bool = False
    api_key: str = ""
    secret_key: str = "change-me-in-production"  # MUST override in production
    access_token_expire_minutes: int = 60 * 24

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # Rate Limiting
    rate_limit_per_minute: int = 60

    # Audit
    audit_enabled: bool = True

    model_config = {"env_prefix": "HLAB_", "env_file": ".env"}


settings = Settings()
