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
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    vision_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    upload_dir: str = "/app/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
