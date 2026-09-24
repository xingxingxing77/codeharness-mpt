"""全局配置。判定 `改`（R7）：替代源 `config2.py`(182) + `utils/yaml_model.py`(48) 那套 yaml 体系。

机制换成 pydantic-settings：`.env` 里用双下划线表达嵌套，例如
`LLM__API_KEY` / `LLM__MAX_TOKEN` / `EMBEDDING__BASE_URL` / `REDIS__HOST`。
字段名一律照源（含源的 `max_token` 单数），以免 S6 逐字复制的源代码取不到属性。
"""
from typing import Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeharness.configs.llm_config import LLMConfig

# C27：embedding 端点**静默截断长输入**的观测点。两个读数：
#   · 重复串前缀（旧探针，仓库外 `E:/tmp/ch_trunc2.py`/`.out`）：3600 字起尾句信号恰为 +0.0000、逐维相同；
#   · **真文档复量**（`tests/manual_embed_truncation.py` 的 A 格，09-23 跑本机 bge-m3）：仓里 docs/*.md
#     拼出的 39,325 字中文散文，全文向量与 `doc[:3200]` 逐维完全相同。
# 取更保守的那个当下界依据——旧台账早写明「3600 是观测上限不是安全值，真中文 token 密度更高」，
# 真文档量出来确实更早（3200 < 3600）。`EMBEDDING__MAX_CHARS` 的上限就钉在这里：超过它＝设了一个不再安全。
EMBEDDING_OBSERVED_TRUNCATION_CHARS = 3200

# C23：`RECALL_FLOOR__MIN_SCORE` 的标定依据与上界。这根线是 **embedding 端点的属性**，不是可以从书上
# 抄的常数：超过它就连「真相关」的切片也进不了 prompt，而症状只是「知识库里没资料」——所以它和
# `EMBEDDING__MAX_CHARS` 一样钉在一次实测上（`tests/manual_recall_floor_curve.py`，C20 那张尺子：
# 生产切块 + RGB_En 300 问，k=5 与生产那一档 k=3 各跑一次）。换模型/换量化必须重量。
# **当前档 = 百炼 `qwen3.7-text-embedding`**（09-24 重标，读数
# `storage/benchmark/recall_floor_curve_qwen3.7-text-embedding_k{5,3}.json`）：
#   · gold 那条切片的 dense 分下界（RGB_En p05）= **0.647**；中文那两栏更高（CRUD 0.6962 / RGB 0.7524）
#     ⇒ 绑定约束是英文那栏。无关切片同刻度实测 0.1472 ⇒ 线放 0.55 两侧都有余量；
#   · 实测代价 `gold_lost_vs_base`（基线 top-k 里有 gold 而这一档没有）：0.40/0.45/0.50 → k=5 全 0；
#     **0.55 → k=5 丢 1/300、k=3 丢 2/300**；0.60 → 丢 3/300；**0.65 → 丢 14/300、砍空率 1.7%、
#     hit@3 从 .9767 掉到 .9333** —— 下坡路从 0.65 开始；
#   · 收益是候选池（每问留下几条，k=5）：15 → 13.69（0.40）/ 7.25（0.50）/ **4.98（0.55）**/ 2.57（0.65）。
# 所以默认取 **0.55**：代价仍在 1~2/300 那一档里的最高线，同时把候选池砍掉三分之二；
# 上界钉在 **0.60**，再往上就是上面那条实测下坡路。
#   · **别拿 hit@1 的档间差当收益**：同码重跑的噪声底本轮实测 k=5 = 0.02、k=3 = 0.0034（bge-m3 那轮
#     0.0067），表里 0.40→0.55 的 hit@1 摆动（.6667→.69）整段都在噪声里。站得住的只有候选池与
#     gold 丢失这两个确定量——这条与 C23 那轮「差点把噪声写成结论」是同一个坑，别第二次踩。
# 历史档（本机 bge-m3，09-23 量）：p05=0.5918、默认 0.40、上界 0.50。那个刻度今天不是生产端点，
# 数字留着只为说明「换端点必重量」这件事本身（旧两份 `recall_floor_curve_k{5,3}.json` 未覆写）。
FLOOR_CALIBRATED_ON = ("qwen3.7-text-embedding (百炼 compatible-mode, 1024d) @ RGB_En 300 问 / "
                       "k=5 与 k=3 两档，工装 tests/manual_recall_floor_curve.py（09-24 重标）")
FLOOR_CALIBRATED_MAX_SCORE = 0.60


class EmbeddingConfig(BaseModel):
    """2026-09-13 选型定稿：bge-m3（1024 维），OpenAI 兼容端点。字段名照源 embedding_config.py。"""

    api_key: str = ""
    base_url: str = "http://localhost:9998/v1"
    model: str = "bge-m3"
    dim: int = 1024
    # C27：入库文本进端点前的**硬上限**（单位=字符数≈中文字数）。默认 1200——真文档量到的端点窗口
    # 是 3200 字，取它的一半以下留余量：窗口按 token 算，同一字符数在不同文本上落点不同，
    # 而块一旦超过窗口，多出来的那段就又只剩「词法腿看得见、语义问不到」。
    max_chars: int = 1200
    # C34：这一腿**不过 gateway 的 `_acall` 重试链**，但它从来就有 openai SDK 自己的重试档——
    # langchain 字段默认 `max_retries=2`，实测「恒回 500 的本机桩收到 3 发」（工装见本项台账）。
    # 提成显式配置的理由：LLM 腿必须归零（SDK 层叠 tenacity 是 9 发且一行日志不出，C25 就是这么收的），
    # 而这里 SDK 那层是**唯一**一层——归零等于让「瞬时抖一下」变成用户上传失败/召回按无资料继续。
    max_retries: int = 2
    # 超时上限。改前 langchain 的 `request_timeout` 默认 None ⇒ httpx `Timeout(timeout=None)`，
    # **行为实测没有上限**（桩睡 25 秒照样成功回来）⇒ 端点挂住会把整场上传无限钉住。
    # 60 秒是 LLM 腿那 300 秒（`const.LLM_API_TIMEOUT`）的收紧版：一次请求最多 20 条短文本，
    # 同时留足本机冷加载模型的时间。最坏 3 发 = 180 秒，与 `_act` 的工具超时同一量级。
    timeout: int = 60

    @field_validator("max_retries")
    @classmethod
    def check_max_retries(cls, v):
        if v < 0:
            raise ValueError(f"EMBEDDING__MAX_RETRIES 不能为负；要「一次都不重发」请显式设 0，收到 {v}")
        return v

    @field_validator("timeout")
    @classmethod
    def check_timeout(cls, v):
        """C5/C27 那条纪律：坏配置当场拒，不许「能跑但行为不对」。
        这一格的特殊之处是 **0 和负值不是「关掉超时」而是「不设上限」**——那正是本项要修的静默挂死，
        所以宁可起不来，也不能让一个笔误把保护摘掉。"""
        if v <= 0:
            raise ValueError(f"EMBEDDING__TIMEOUT 必须 >0：0/负值不等于关掉超时，而是回到"
                             f"「端点挂住就一直等」的无上限状态，收到 {v}")
        return v

    @field_validator("max_chars")
    @classmethod
    def check_max_chars(cls, v):
        """C5 那条纪律：坏配置不许「能跑但行为不对」，要**当场拒**。

        非正值不是「关掉切块」的开关——关掉它就等于回到本项要修的静默截断；超过观测截断点则形同
        「以为设过了」。两者都在 settings 构造期抛，起不来比静默少一层保护便宜。"""
        if v <= 0:
            raise ValueError(f"EMBEDDING__MAX_CHARS 必须是正整数，收到 {v}（设 0 不等于不切，是要静默截断）")
        if v > EMBEDDING_OBSERVED_TRUNCATION_CHARS:
            raise ValueError(f"EMBEDDING__MAX_CHARS={v} 超过实测截断点 "
                             f"{EMBEDDING_OBSERVED_TRUNCATION_CHARS} 字，这块上限就不再保尾段可达")
        return v


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


class RecallFloorConfig(BaseModel):
    """C23：召回的相关性下限。`memory/longterm.py::recall` 的闸门。

    **先说清一件容易骗人的事：`min_score` 的刻度随 `mode` 变**——它是「哪个模型的哪条腿的分」的函数，
    不是能从书上抄的常数：
      · `mode=score`  → dense 腿的**余弦**（bge-m3 那一档已标定，见 `FLOOR_CALIBRATED_*`；换端点必重量）；
      · `mode=rerank` → 精排回的 **relevance_score**（另一个刻度，本机**尚未标定**，只有 3 个样本点）；
      · `mode=rank`   → 不用分，按 dense 名次（序数）⇒ **换 embedding 模型时只有这一档免标定**。
    下限为什么不许打在 hybrid 的返回分上：那条腿是服务端 RRF **名次分**，与相似度不可比
    （`document_store/exp_store.py:7` 同一口径——经验池因此只走 dense 才敢跟 0.9 比）。

    四档：
      · `off`    —— 改前那条单发路径，行为与既有判据逐字不变；
      · `score`  —— (a) 支：dense 取 `k*oversample` 当候选窗，余弦 < 线 的出局，剩下的交 hybrid 重排；
      · `rank`   —— (c) 支：同一个窗按 dense 原始名次 ≤ `max_rank` 出局，再交 hybrid 重排；
      · `rerank` —— (b) 支：粗排取 `max(k, reranker.recall_k)` 条 → 送精排打分 → 按 relevance 筛 → 前 k。
                    精排不可用（没配 / 调用失败）时**不许冒充有下限**：退回粗排原序前 k 条，
                    那一声 warning 在 `rerank_scored` 里响，全场只响一次。
    """

    mode: Literal["off", "score", "rank", "rerank"] = "score"
    oversample: int = 3        # dense 候选窗 = k × 此数（≥1；rerank 档不用它，它用 reranker.recall_k）
    min_score: float = 0.55    # score 档的余弦下限：新刻度上「代价仍在 1~2/300 里」的最高档（依据见上面常量）
    max_rank: int = 5          # rank 档的名次上限（≥1）

    @field_validator("min_score")
    @classmethod
    def check_min_score(cls, v):
        if v < 0 or v > 1:
            raise ValueError(f"RECALL_FLOOR__MIN_SCORE 必须在 [0,1]（余弦与精排分都在这个刻度），收到 {v}")
        return v

    @model_validator(mode="after")
    def check_active_mode(self):
        """坏配置不许「能跑但行为不对」：开了哪一档就把那一档的参数判实，与 C27 的 max_chars 同纪律。

        `score` 档留 0 等于开了闸又什么都不砍（还白花一次 dense 预查）；大到超过标定上界的线
        等于「以为设过了」——真相关的那批也过不去，召回从此恒空，而界面看上去只是「知识库里没资料」。
        """
        if self.oversample < 1:
            raise ValueError(f"RECALL_FLOOR__OVERSAMPLE 必须 ≥1，收到 {self.oversample}")
        if self.mode == "score" and not (0 < self.min_score <= FLOOR_CALIBRATED_MAX_SCORE):
            raise ValueError(
                f"RECALL_FLOOR__MODE=score 要求 0 < MIN_SCORE <= {FLOOR_CALIBRATED_MAX_SCORE}"
                f"（{FLOOR_CALIBRATED_ON}；超过它连尺子上真相关的切片都进不来），收到 {self.min_score}")
        if self.mode == "rank" and self.max_rank < 1:
            raise ValueError(f"RECALL_FLOOR__MODE=rank 要求 MAX_RANK ≥1，收到 {self.max_rank}")
        # rerank 档只判「线在刻度内」，**不判上界**：那根线还没标定（本机只有 3 个样本点），
        # 拿未标定的数当上界比不设界更骗人。
        if self.mode == "rerank" and self.min_score <= 0:
            raise ValueError("RECALL_FLOOR__MODE=rerank 要求 0 < MIN_SCORE <= 1（精排 relevance 刻度，"
                             f"未标定），收到 {self.min_score}")
        return self


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
    # C28：停机时**最多**等多久把队列里的 span 发完。SDK 自己的 `shutdown()` 没有任何上界
    # （`flush()` 里三个 `Queue.join()` + `_stop_and_join_consumer_threads()` 里逐个 `Thread.join()`，
    # 四处都不带超时；构造期那个 `timeout=` 只管**单次 HTTP 尝试**），所以端点不可达时墙钟没有上界
    # ——09-23 现取：s15 默认档跑到第九组卡住 150 秒未出，而 `LANGFUSE__ENABLED=0` 同一份 30 秒跑完。
    # 到点就走并丢掉没发完的那批（唯一会留下的降级，且它喊出来）。真要全发完就把这个调大。
    shutdown_grace_sec: int = 5

    @field_validator("shutdown_grace_sec")
    @classmethod
    def check_grace(cls, v):
        """0 不是「不等」的开关，是「每次停机都必定丢数据且没人知道」——要 0 请显式把开关关掉。"""
        if v <= 0:
            raise ValueError(f"LANGFUSE__SHUTDOWN_GRACE_SEC 必须是正整数（秒），收到 {v}"
                             "（设 0 不等于不采，是让停机永远静默丢弃）")
        return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    recall_floor: RecallFloorConfig = RecallFloorConfig()    # C23
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

    @model_validator(mode="after")
    def check_rerank_floor_needs_a_service(self):
        """C23(b) 那条「不许出现第三种静默降级」的配置面：开了 `rerank` 档却没配精排服务，
        等于**每一次 recall 都静默没有下限**（`rerank_scored` 会原样退回粗排序）。这种组合不许启动成功。"""
        if self.recall_floor.mode == "rerank" and not self.reranker.base_url:
            raise ValueError("RECALL_FLOOR__MODE=rerank 需要 RERANKER__BASE_URL：没配精排却有这道闸，"
                             "每次 recall 都会静默降级成「无下限」（正是 C8 判过的那族）")
        return self


settings = Settings()
