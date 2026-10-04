"""@exp_cache：语义对齐源 exp_pool/decorator.py:29——命中即复用，未命中执行后入库。

与源的四处分叉（都是判定表里写死的）：
- `perfect_judges` 判 `推迟`：源用 LLM 判「这条经验配不配当前问题」；这里判定 = dense 余弦
  >= threshold（默认 0.97，C167 定档——C164 活体读数的带分离：跨角色/跨任务 CMD_PROMPT 落
  0.901~0.935 的危险带，同任务复用带 0.984~1.0；0.9 坐在危险带内对误命中开门）。打分那半
  （C165，用户 10-04 拍「现在接线」）走
  `enable_score`（默认关——可选那级不默认烧钱）：开了才在入库前调 `SimpleScorer`，
  分数**只存账不拦截**（判定闸仍是余弦阈值，分数有没有资格参判要等 C164 真命中率读数）；
- `context_builders` 不搬：源把落选经验注入 prompt 的 EXPERIENCE_MASK 槽，本仓 `_think` 的
  experience 槽已由长期记忆召回喂着（S5.2），不再叠第二路；
- 只支持 async 函数（源为 ActionNode 同步线保留的 `choose_wrapper`/NestAsyncio 在本仓零调用场景）；
- 经验池整体受 `settings.exp_pool` 开关控制（enabled / enable_read / enable_write / enable_score）。

源契约保留：**kw 必须带 `req`**；tag 缺省 = `类名.方法名`（源 `_generate_tag:202`），本仓再加角色名段（C168）。
存储、计数与打分故障只 warning 不断主流程（源 `handle_exception` 的鲁棒语义）。
"""
from functools import wraps
from typing import Callable, Optional, TypeVar

from codeharness.configs.settings import settings
from codeharness.exp_pool.manager import ExperienceManager, get_exp_manager
from codeharness.exp_pool.schema import LOG_NEW_EXPERIENCE_PREFIX, Experience, Metric, QueryType
from codeharness.exp_pool.scorers import BaseScorer, SimpleScorer
from codeharness.exp_pool.serializers import BaseSerializer, SimpleSerializer
from codeharness.logs import logger

ReturnType = TypeVar("ReturnType")


def exp_cache(
    _func: Optional[Callable[..., ReturnType]] = None,
    query_type: QueryType = QueryType.SEMANTIC,
    manager: Optional[ExperienceManager] = None,
    serializer: Optional[BaseSerializer] = None,
    tag: Optional[str] = None,
    threshold: float = 0.97,
    scorer: Optional[BaseScorer] = None,
):
    """Decorator to get a perfect experience, otherwise, executes the function and creates a new one.

    Args:
        query_type: SEMANTIC=向量近即复用；EXACT=req 逐字相等才复用。
        manager/serializer: 缺省在调用时现取单例（源 ExpCacheHandler.initialize 的惰性同款）。
        threshold: 余弦判定线。C164 活体定档 0.97（C167）：真异题/跨角色的 CMD_PROMPT 落 0.93 带、
            同题复用落 0.98+ 带，线要坐在两带之间；误命中代价（执行错经验）远大于 miss（多一发 LLM）。
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cfg = settings.exp_pool
            if not cfg.enabled:
                return await func(*args, **kwargs)
            if "req" not in kwargs:
                raise ValueError("`req` must be provided as a keyword argument.")
            ser = serializer or SimpleSerializer()
            mgr = manager or get_exp_manager()
            exp_tag = tag or _auto_tag(args, func)
            req_s = ser.serialize_req(**kwargs)

            if cfg.enable_read:
                try:
                    pairs = await mgr.query_exps(req_s, tag=exp_tag, query_type=query_type)
                except Exception as e:
                    logger.warning(f"经验池读取失败，照常执行: {type(e).__name__}: {e}")
                    pairs = []
                for exp, score in pairs:
                    if score >= threshold:
                        await _safe(mgr.record_hit(exp), "命中计数")
                        logger.info(f"经验命中 (tag={exp_tag}, sim={score:.3f})：跳过本次 LLM 调用")
                        return ser.deserialize_resp(exp.resp)

            result = await func(*args, **kwargs)

            if cfg.enable_write:
                exp = Experience(req=req_s, resp=ser.serialize_resp(result), tag=exp_tag)
                if cfg.enable_score:
                    await _attach_score(exp, scorer, req_s, exp.resp)
                await _safe(mgr.create_exp(exp), "经验入库")
                # C163：正文不上日志（req 是用户原话、resp 是模型回包，整段落 DEBUG 只会堆日志）——
                # 留 tag/uuid/长度够对账。前缀常量在 schema（源同名常量，此前零消费者）。
                logger.debug(f"{LOG_NEW_EXPERIENCE_PREFIX}tag={exp_tag} uuid={exp.uuid} "
                             f"req_len={len(req_s)} resp_len={len(exp.resp)}")
            return result
        return wrapper

    return decorator(_func) if _func else decorator


async def _attach_score(exp: Experience, scorer: Optional[BaseScorer], req: str, resp: str) -> None:
    """C165：入库前打质量分。失败只 warning、metric 留空照常入库——质量分永远不能挡住经验池主路；
    也不造分（打不了就空着，与判定表「留 None 不造数据」同一条纪律）。"""
    try:
        s = scorer or SimpleScorer()
        exp.metric = Metric(score=await s.evaluate(req, resp))
    except Exception as e:
        logger.warning(f"经验打分失败（照常入库）: {type(e).__name__}: {e}")


def _auto_tag(args, func) -> str:
    """源 `_generate_tag:202`：挂在方法上记 `类名.方法名`，裸函数记函数名。

    C168：再加一段**角色名**（`类名.角色名.方法名`）。dynamic 线三个角色都是 `RoleZero` 实例，
    只到类名 ⇒ 同一个 tag，而 `exp_store.search` 按 action_tag 等值过滤（硬过滤，不是软阈值），
    于是队长的 think 能复用成员那轮的经验——命中的是一条**带 args 的 ZeroThought**，等于执行
    别人的命令（C164 冷写场现证 12 发跨角色命中）。名字取自 `profile`（装配期就定，见 team.py），
    认不出 profile 形态的类退回旧形状，不为它造第二套 tag 语法。"""
    if args and hasattr(args[0], "__class__") and not isinstance(args[0], type):
        prof = getattr(args[0], "profile", None)
        role = prof.get("name", "") if isinstance(prof, dict) else ""
        return ".".join(p for p in (type(args[0]).__name__, role, func.__name__) if p)
    return func.__name__


async def _safe(coro, what: str):
    try:
        await coro
    except Exception as e:
        logger.warning(f"经验池{what}失败（不影响本次调用）: {type(e).__name__}: {e}")
