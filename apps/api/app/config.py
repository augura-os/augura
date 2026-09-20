"""Runtime configuration via environment variables (contract AGENT_SPEC §8)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg2://augura:augura@localhost:5432/augura"
    )
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "augura123"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "augura"
    minio_secret_key: str = "augura123"
    minio_bucket: str = "creative-assets"
    # MinIO 是否走 TLS：compose 默认 plain http，生产可经 MINIO_SECURE=true 开启（P2-26）
    minio_secure: bool = False
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    vision_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    upload_dir: str = "/app/uploads"
    # 单文件上传大小上限（字节），默认 1 GB；超限返回 413（UPLOAD_MAX_BYTES 可调）
    upload_max_bytes: int = 1024 * 1024 * 1024
    # 同时运行的分析流水线数上限：每个 pipeline 在整个 LLM 调用期间持有
    # 一个 DB session，不封顶会把连接池打爆（批量上传 = 每文件一个后台任务）。
    # F3 双槽模型：本槽只覆盖分析核心（抽帧/LLM/嵌入/聚类 + completed 落库），
    # 判定阶段在槽释放后走下面的 judge_concurrency 独立限流
    analysis_concurrency: int = 3
    # 同时运行的判定（judge）阶段数上限：判定同样长时间持有 session 跑 LLM，
    # 独立封顶——判定卡住只占判定槽，分析吞吐与连接池不再被拖死（F3 事故）
    judge_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
