"""@exp_cache：语义对齐源 exp_pool/decorator.py:29——命中即复用，未命中执行后入库。

与源的四处分叉（都是判定表里写死的）：
- `perfect_judges`/`scorers` 判 `推迟`：源用 LLM 判「这条经验配不配当前问题」、入库时再 LLM 打分；
  这里判定 = dense 余弦 >= threshold（默认 0.9，宁缺勿滥），`Metric.score` 留 None 不造数据；
- `context_builders` 不搬：源把落选经验注入 prompt 的 EXPERIENCE_MASK 槽，本仓 `_think` 的
  experience 槽已由长期记忆召回喂着（S5.2），不再叠第二路；
- 只支持 async 函数（源为 ActionNode 同步线保留的 `choose_wrapper`/NestAsyncio 在本仓零调用场景）；
- 经验池整体受 `settings.exp_pool` 三开关控制（enabled / enable_read / enable_write，字段照源）。

源契约保留：**kw 必须带 `req`**；tag 缺省 = `类名.方法名`（源 `_generate_tag:202`）。
存储与计数故障只 warning 不断主流程（源 `handle_exception` 的鲁棒语义）。
"""
from functools import wraps
from typing import Callable, Optional, TypeVar

from codeharness.configs.settings import settings
from codeharness.exp_pool.manager import ExperienceManager, get_exp_manager
from codeharness.exp_pool.schema import Experience, QueryType
from codeharness.exp_pool.serializers import BaseSerializer, SimpleSerializer
from codeharness.logs import logger

ReturnType = TypeVar("ReturnType")


def exp_cache(
    _func: Optional[Callable[..., ReturnType]] = None,
    query_type: QueryType = QueryType.SEMANTIC,
    manager: Optional[ExperienceManager] = None,
    serializer: Optional[BaseSerializer] = None,
    tag: Optional[str] = None,
    threshold: float = 0.9,
):
    """Decorator to get a perfect experience, otherwise, executes the function and creates a new one.

    Args:
        query_type: SEMANTIC=向量近即复用；EXACT=req 逐字相等才复用。
        manager/serializer: 缺省在调用时现取单例（源 ExpCacheHandler.initialize 的惰性同款）。
        threshold: 余弦判定线。同一个问题的两次措辞也有差异，0.9 之上复用才是安全的。
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
                await _safe(mgr.create_exp(exp), "经验入库")
                logger.debug(f"New experience: {exp.model_dump_json(include={'uuid', 'req', 'resp', 'tag'})}")
            return result
        return wrapper

    return decorator(_func) if _func else decorator


def _auto_tag(args, func) -> str:
    """源 `_generate_tag:202`：挂在方法上记 `类名.方法名`，裸函数记函数名。"""
    if args and hasattr(args[0], "__class__") and not isinstance(args[0], type):
        return f"{type(args[0]).__name__}.{func.__name__}"
    return func.__name__


async def _safe(coro, what: str):
    try:
        await coro
    except Exception as e:
        logger.warning(f"经验池{what}失败（不影响本次调用）: {type(e).__name__}: {e}")
