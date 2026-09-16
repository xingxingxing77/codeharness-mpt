"""全局配置。判定 `改`（R7）：替代源 `config2.py`(182) + `utils/yaml_model.py`(48) 那套 yaml 体系。

机制换成 pydantic-settings：`.env` 里用双下划线表达嵌套，例如
`LLM__API_KEY` / `LLM__MAX_TOKEN` / `EMBEDDING__BASE_URL` / `REDIS__HOST`。
字段名一律照源（含源的 `max_token` 单数），以免 S6 逐字复制的源代码取不到属性。
"""
from typing import Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeharness.configs.llm_config import LLMConfig


class EmbeddingConfig(BaseModel):
    """2026-09-13 选型定稿：bge-m3（1024 维），OpenAI 兼容端点。字段名照源 embedding_config.py。"""

    api_key: str = ""
    base_url: str = "http://localhost:9998/v1"
    model: str = "bge-m3"
    dim: int = 1024


class RerankerConfig(BaseModel):
    model: str = "bge-reranker-v2-m3"
    api_key: str = ""
    base_url: str = "http://localhost:9998/v1"      # /v1/rerank
    top_n: int = 5
    recall_k: int = 10                                # 粗排取 10 → 精排 top_n=5；服务离线自动降级为仅粗排


class QdrantConfig(BaseModel):
    """源 qdrant_config.py + document_store/qdrant_store.py:11 的 QdrantConnection 字段合并。"""

    host: Optional[str] = None
    port: Optional[int] = None
    url: str = "http://localhost:6333"
    api_key: str = ""
    collection_prefix: str = "codeharness"    # R9：单 collection 的名字，租户靠 payload 隔离


class RedisConfig(BaseModel):
    """来源：metagpt/configs/redis_config.py（26 行）。`to_url()` 语义照源，供 utils/redis.py 用。"""

    host: str = "localhost"
    port: int = 6379
    username: Optional[str] = None
    password: Optional[str] = None
    db: int = 0
    ssl: bool = False

    def to_url(self) -> str:
        scheme = "rediss" if self.ssl else "redis"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"


class ExpPoolConfig(BaseModel):
    """字段名与默认值照源 configs/exp_pool_config.py（默认全关，显式开了接线才生效）。
    源的 retrieval_type/persist_path/collection_name/use_llm_ranker 随「存储换 Qdrant +
    Redis 命中计数」的判定失去意义（chroma/bm25 双存储与 LLM ranker 都不搬），不带过来。"""

    enabled: bool = False
    enable_read: bool = False
    enable_write: bool = False


class SearchConfig(BaseModel):
    """源 search_config.py：只保留新栈实际接的两个引擎（ddg + serper）。"""

    tbs: Optional[str] = None
    domaits: bool = True
    serper_api_key: str = ""
    bing_api_key: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    qdrant: QdrantConfig = QdrantConfig()
    redis: RedisConfig = RedisConfig()
    search: SearchConfig = SearchConfig()
    exp_pool: ExpPoolConfig = ExpPoolConfig()

    workspace_root: str = "./workspace"
    memory_overflow_size: int = 200
    enable_rag: bool = True
    # 源 config2.py:79 CodeValidateConfig.code_validate_k_times——WriteCodeReview 的 评审→重写 轮数，
    # 源默认 2；初版记成"照源默认 1"（实为抄录错），2026-09-16 对账改回 2
    code_validate_k_times: int = 2

    # 源 config2.py 的 Config 级开关（repair.py 与 gateway 的重试层读它）
    repair_llm_output: bool = True
    multimodal_llm: Optional[LLMConfig] = None


settings = Settings()
