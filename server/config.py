"""Global configuration via pydantic-settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


def env_field(default: Any, name: str) -> Any:
    """Use AEZAB_ as the primary prefix while accepting legacy HLAB_ names."""
    return Field(default, validation_alias=AliasChoices(f"AEZAB_{name}", f"HLAB_{name}"))


class Settings(BaseSettings):
    # App
    app_name: str = env_field("Aezab", "APP_NAME")
    debug: bool = env_field(False, "DEBUG")
    api_prefix: str = env_field("/api/v1", "API_PREFIX")

    # Database
    database_url: str = env_field("sqlite+aiosqlite:///./data/aezab.db", "DATABASE_URL")

    # Redis
    redis_url: str = env_field("redis://localhost:6379/0", "REDIS_URL")

    # LLM
    llm_provider: Literal["openai_compatible", "dashscope", "zhipu", "local"] = env_field(
        "openai_compatible", "LLM_PROVIDER"
    )
    llm_base_url: str = env_field("http://localhost:11434/v1", "LLM_BASE_URL")
    llm_api_key: str = env_field("", "LLM_API_KEY")
    llm_model: str = env_field("qwen-flash", "LLM_MODEL")
    llm_temperature: float = env_field(0.3, "LLM_TEMPERATURE")
    llm_max_tokens: int = env_field(2048, "LLM_MAX_TOKENS")
    llm_timeout: int = env_field(60, "LLM_TIMEOUT")  # Per-request LLM call timeout in seconds

    # Embedding
    embedding_provider: Literal["local", "dashscope", "openai_compatible"] = env_field(
        "local", "EMBEDDING_PROVIDER"
    )
    embedding_model: str = env_field("BAAI/bge-m3", "EMBEDDING_MODEL")
    embedding_dim: int = env_field(1024, "EMBEDDING_DIM")
    hf_endpoint: str = env_field("https://hf-mirror.com", "HF_ENDPOINT")

    # Knowledge upload
    knowledge_max_upload_mb: int = env_field(50, "KNOWLEDGE_MAX_UPLOAD_MB")

    # Speech-to-text / ASR
    asr_provider: Literal["dashscope_qwen", "funasr_http", "openai_compatible", "disabled"] = env_field(
        "dashscope_qwen", "ASR_PROVIDER"
    )
    asr_base_url: str = env_field("https://dashscope.aliyuncs.com/compatible-mode/v1", "ASR_BASE_URL")
    asr_api_key: str = env_field("", "ASR_API_KEY")
    asr_model: str = env_field("qwen3-asr-flash", "ASR_MODEL")
    asr_timeout: int = env_field(60, "ASR_TIMEOUT")
    asr_max_file_mb: int = env_field(10, "ASR_MAX_FILE_MB")
    asr_funasr_path: str = env_field("/transcribe", "ASR_FUNASR_PATH")
    asr_config_path: str = env_field("./data/asr_config.json", "ASR_CONFIG_PATH")

    # Vector Store
    vector_store: Literal["faiss", "pgvector", "milvus", "auto"] = env_field("auto", "VECTOR_STORE")
    faiss_index_dir: str = env_field("./data/vectors", "FAISS_INDEX_DIR")
    faiss_index_path: str = env_field("./data/vectors/faiss.index", "FAISS_INDEX_PATH")
    milvus_uri: str = env_field("localhost:19530", "MILVUS_URI")

    # CORS
    cors_origins: str = env_field("*", "CORS_ORIGINS")

    # Auth
    disable_auth: bool = env_field(False, "DISABLE_AUTH")
    api_key: str = env_field("", "API_KEY")
    secret_key: str = env_field("change-me-in-production", "SECRET_KEY")  # MUST override in production
    access_token_expire_minutes: int = env_field(60 * 24, "ACCESS_TOKEN_EXPIRE_MINUTES")

    # JWT
    jwt_algorithm: str = env_field("HS256", "JWT_ALGORITHM")
    jwt_expire_minutes: int = env_field(1440, "JWT_EXPIRE_MINUTES")  # 24 hours

    # Rate Limiting
    rate_limit_per_minute: int = env_field(60, "RATE_LIMIT_PER_MINUTE")

    # Audit
    audit_enabled: bool = env_field(True, "AUDIT_ENABLED")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


settings = Settings()
