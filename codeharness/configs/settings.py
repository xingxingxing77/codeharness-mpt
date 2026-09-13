"""全局配置。替代源项目 config2.py + configs/（约 300 行 yaml 体系）。"""
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 0.0
    max_tokens: int = 4096


class EmbeddingConfig(BaseModel):
    """2026-09-13 选型：bge-m3（1024 维），OpenAI 兼容端点"""
    model: str = "bge-m3"
    api_key: str = ""
    base_url: str = "http://localhost:9998/v1"
    dim: int = 1024


class RerankerConfig(BaseModel):
    model: str = "bge-reranker-v2-m3"
    base_url: str = "http://localhost:9998/v1"   # /v1/rerank
    top_n: int = 5


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    qdrant: QdrantConfig = QdrantConfig()
    workspace_root: str = "./workspace"
    memory_overflow_size: int = 200
    max_budget: float = 10.0


settings = Settings()