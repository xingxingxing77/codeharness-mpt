from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

class LLMConfig(BaseModel):
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 0.0
    max_tokens: int = 4096

class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    collection_prefix: str = "codeharness"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = LLMConfig()
    qdrant: QdrantConfig = QdrantConfig()
    workspace_root: str = "./workspace"
    memory_overflow_size: int = 200      # 对齐 role_zero_memory 的溢出阈值
    max_budget: float = 10.0             # 对齐 team.py:41 investment 语义

settings = Settings()