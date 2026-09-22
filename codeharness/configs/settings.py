"""全局配置。判定 `改`（R7）：替代源 `config2.py`(182) + `utils/yaml_model.py`(48) 那套 yaml 体系。

机制换成 pydantic-settings：`.env` 里用双下划线表达嵌套，例如
`LLM__API_KEY` / `LLM__MAX_TOKEN` / `EMBEDDING__BASE_URL` / `REDIS__HOST`。
字段名一律照源（含源的 `max_token` 单数），以免 S6 逐字复制的源代码取不到属性。
"""
from typing import Optional
from urllib.parse import quote

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
    # 精排是**可选**能力，默认不配：以前缺省指向 http://localhost:9998/v1，而 `.env` 与
    # `docker-compose.yml` 都没有这个服务，`longterm._rerank` 的判据又只挡「URL 非空」
    # → 每次 recall 都白等一次 HTTP、再被 except 吞掉降级 + 刷 warning（对照2 §四-3）。
    # 要启用精排：设 `RERANKER__BASE_URL`（离线服务在时那一跳由 s5 t28 钉）。
    base_url: str = ""
    top_n: int = 5
    recall_k: int = 10                                # 粗排取 10 → 精排 top_n=5；未配置或服务离线都降级为仅粗排


class ToolRecallConfig(BaseModel):
    """C6 工具召回（`tools/tool_recall.py`）。默认值=**今天不生效**，理由都写在读数里：

    现场实测：名册 18 只、18 条描述全量进 prompt 是 1455 字符；而召回漏一次，模型吐「未知命令」
    被回喂、多烧一整发（`step-3.5-flash` 一句 ¥0.12–0.27）。所以长线钉在 30——
    名册真长大了才开始裁（`docs/对照3-工具调用.md` §六-5 同一口径）。
    `use_llm` 默认 False 与 `RerankerConfig.base_url=""` 同一条纪律：可选的那一级不许默认白烧钱/白等。
    """

    min_tools: int = 30       # 名册 ≤ 此数 ⇒ 不裁，prompt 逐字不变
    recall_topk: int = 12     # 词法粗筛留 12 只（源的 20 是给 27~31 只池子用的，本仓按比例收）
    topk: int = 6             # 最终进 prompt 的数量（源同款 5，这里留 6 给「写+读+终端」这类组合）
    use_llm: bool = False     # 开=每轮 think 多发一次模型调用；门禁用 FakeLLM 验形状，真读数按 ADR-02 批
    semantic: bool = True     # 粗筛带语义腿（bge-m3 dense，走 `EMBEDDING__*`）；服务不可用时自动退词法腿并留话
    always: str = "read_file,write_file,terminal_command,execute_shell_async"   # 常驻集：召回不裁它们


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
        # S5：凭据以前被静默丢掉。`platforms/` 五件 + `server/runner` 的跨 worker 控制通道 +
        # `utils/redis` 全走这一个出口——compose 里给 redis 上了 --requirepass 之后，
        # URL 不带凭据就是全线 NOAUTH（会话态/事件流/审批台账一起断）。
        # quote(safe="") 不是讲究：密码里能有 @ : /，不编码等于把分隔符拼进 URL。
        auth = ""
        if self.password or self.username:
            auth = f"{quote(self.username or '', safe='')}:{quote(self.password or '', safe='')}@"
        return f"{scheme}://{auth}{self.host}:{self.port}/{self.db}"


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


class PlatformConfig(BaseModel):
    """S7 平台层开关（施工4 目标结构）。默认全关=进程内实现，feature flag 双跑、全绿才切默认。
    限流是 N5 拆分后保留的那一半：做请求数/并发，**不是金额预算**（§零）。"""

    use_redis: bool = True            # PLATFORM__USE_REDIS：会话态/事件流/插话/控制通道走 Redis
    # （S7 双实现全绿后切默认，2026-09-17；显式置 0 仍走进程内旧路——双配置门禁常驻钉两头）
    create_per_min: int = 30          # 建会话固定窗限流（HTTP 入口 429，不进图）
    max_concurrent: int = 16          # 同时 running 会话数上限
    rate_window_sec: int = 60
    auth_enabled: bool = False        # N1 账号边界：默认关=单机开发态（15 门禁零破坏）；置 1 强制登录，
                                      # session/记忆/配额按 user_id 隔离。生产多租户部署显式开启。


class LangfuseConfig(BaseModel):
    """N9 外部可观测（本机自托管 Langfuse v4）。字段不叫 `LANGFUSE_PUBLIC_KEY` 那套——
    本仓的 `.env` 走 pydantic-settings（`LANGFUSE__*`），SDK 自读的 os.environ 拿不到，
    接线处显式构造客户端（codeharness/observability.py）。默认关：显式开 + 双 key 齐才生效。"""

    enabled: bool = False
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: str = ""
    timeout: int = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    qdrant: QdrantConfig = QdrantConfig()
    redis: RedisConfig = RedisConfig()
    search: SearchConfig = SearchConfig()
    exp_pool: ExpPoolConfig = ExpPoolConfig()
    tool_recall: ToolRecallConfig = ToolRecallConfig()
    platform: PlatformConfig = PlatformConfig()
    langfuse: LangfuseConfig = LangfuseConfig()

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
