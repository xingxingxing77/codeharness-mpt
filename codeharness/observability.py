"""N9 外部可观测接缝：自托管 Langfuse v4 承接全链路 trace（判定清单 §五 N9）。

三件事，覆盖 agent 运行的可观测面：
1. `callbacks()` —— 塞进 `RunnableConfig["callbacks"]`（唯一注入点 `runner._prepare`）。
   图节点 / 每次 LLM 调用（含 structured 与流式）/ `@tool` 工具调用 / 错误事件
   全部靠 LangChain callback 自动成 span：节点内裸 `model.ainvoke()`、`tool.ainvoke()`
   经 langchain-core 的 `var_child_runnable_config` 继承拿到 handler。
2. `session_attributes()` —— 一场会话 = 一个 Langfuse session（session_id/user/tags/trace_name）。
   ⚠ v4 的 LangChain handler 已不读 `metadata["langfuse_session_id"]`，只能靠 OTel 上下文属性。
3. `span()` —— 不在 callback 面内的手工路径（记忆检索的 embedding / Qdrant / rerank 是裸调用）。

**默认关**（`LangfuseConfig.enabled=False`）：SDK 缺失、开关关、双 key 缺任一时全部 no-op——
空 list / nullcontext / 原函数直通，链路零开销、行为与未接入时逐字一致。
SDK **懒导入**：内核（memory）在本模块挂了装饰器，未接入的进程连 import 开销都不付。

客户端**显式构造**：pydantic-settings 不会把 `.env` 写进 `os.environ`，SDK 自读的
`LANGFUSE_PUBLIC_KEY` 那套拿不到本仓配置（本仓键名是 `LANGFUSE__*`）。

与自研 N4 trace（`platforms/trace.py` → Redis → 前端面板）双轨并存，互不替代。
"""
from __future__ import annotations

import atexit
import threading
from contextlib import nullcontext
from functools import wraps

_sdk = None                 # None=未加载；False=未装；否则 (Langfuse, propagate_attributes, CallbackHandler)
_client = None
_handler = None


def _mod():
    """懒导入：未装 SDK 的机器整模块降级，import 不炸。"""
    global _sdk
    if _sdk is None:
        try:
            from langfuse import Langfuse, propagate_attributes
            from langfuse.langchain import CallbackHandler
            _sdk = (Langfuse, propagate_attributes, CallbackHandler)
        except Exception:
            _sdk = False
    return _sdk


def enabled() -> bool:
    """SDK + 开关 + 双 key 三者齐备才算开。缺一即 no-op——不做「半截链路」的静默降级。"""
    from codeharness.configs.settings import settings
    c = settings.langfuse
    return bool(_mod() and c.enabled and c.public_key and c.secret_key)


def client():
    if not enabled():
        return None
    global _client
    if _client is None:
        import warnings
        # N9 伴随噪音，在接缝处一次滤掉：handler 把 structured 链（include_raw 形态）
        # 的输出序列化给 OTel 时，pydantic 打 PydanticSerializationUnexpectedValue
        # （raw/parsed 字段类型对不上）——只影响日志观感，span 数据完整（真会话实测）。
        warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
        from codeharness.configs.settings import settings
        Langfuse = _mod()[0]
        c = settings.langfuse
        _client = Langfuse(public_key=c.public_key, secret_key=c.secret_key,
                           host=c.host, tracing_enabled=True, timeout=c.timeout)
    return _client


def callbacks() -> list:
    """注入点只有一个：`runner._prepare`。节点内/网关里**不得**再传一次——同一 handler
    既显式传又靠继承会双 span。handler 进程级单例：v4 内部状态按 run_id 键控，并发会话安全。"""
    global _handler
    if not enabled():
        return []
    if _handler is None:
        from codeharness.configs.settings import settings
        client()                        # handler 读全局 client，必须先建
        # ⚠ 必须带 public_key：SDK 的 get_client() 在进程内出现过第二个项目时返回
        # **disabled client**（防跨项目串数据）——不带 key 的 handler 会静默零导出（实测）。
        _handler = _mod()[2](public_key=settings.langfuse.public_key)
    return [_handler]


def session_attributes(session, project: str = ""):
    """包住整段 `astream_events` 循环（`runner._session_ctx` 是两条路唯一的上下文口）。
    按 asyncio task 隔离：并发会话各持一份 OTel 上下文，不串。"""
    if not enabled() or session is None:
        return nullcontext()
    from codeharness.configs.settings import settings
    propagate_attributes = _mod()[1]
    paradigm = getattr(session, "paradigm", "classic") or "classic"
    return propagate_attributes(
        session_id=getattr(session, "id", "") or "",
        user_id=getattr(session, "user_id", "") or "default",
        tags=[paradigm, settings.llm.model],
        trace_name=f"{paradigm}:{project or getattr(session, 'project_name', '')}")


def span(name: str, as_type: str = "span"):
    """手工 span 装饰器（记忆检索等不在 callback 面内的裸调用）。关时原函数直通，零开销。

    ⚠ 用 `client().start_as_current_observation` 显式绑定本项目客户端，**不用** SDK 的
    `observe` 装饰器——后者内部走 `get_client()`（不带 key），进程里出现过第二个项目时
    返回 disabled client，手工 span 静默零导出（与 handler 同款坑，2026-09-18 实测）。
    不采集入参/返回值：LLM 的 prompt 与产出已由 callback 面全覆盖，这里只要名字/类型/耗时。"""
    def deco(fn):
        @wraps(fn)
        async def call(*args, **kwargs):
            if not enabled():
                return await fn(*args, **kwargs)
            with client().start_as_current_observation(name=name, as_type=as_type):
                return await fn(*args, **kwargs)
        return call
    return deco


def flush():
    """短脚本收尾用（长驻 server 靠 SDK 后台定时 flush，停机走 shutdown）。
    ⚠ 这一条是**无界**等待，且是故意的：调它的脚本就是要「发完再继续」。无界的问题只在停机路径上，
    那一条由 `shutdown()` 的 grace 管（C28）。"""
    if enabled() and _client is not None:
        _client.flush()


def shutdown() -> bool:
    """长驻进程退出钩子：等 flush，但**最多等 `LANGFUSE__SHUTDOWN_GRACE_SEC` 秒**（C28）。
    返回 `True` = 按时冲完；`False` = grace 用尽、这批里有没发出去的 span（调用方不必处理，
    给它一个可读的值是为了让门禁能断言「不许静默」，而不是去猜日志）。

    为什么不直接调 SDK 的 `shutdown()`：它 = `flush()`（三个 `Queue.join()`）
    + `_stop_and_join_consumer_threads()`（逐个 `Thread.join()`），**四处都不带超时**；构造期传的
    `timeout=` 只约束单次 HTTP 尝试，端点不可达时每次尝试各退各的 ⇒ 墙钟没有上界。
    09-23 现取：`.env` 默认 `LANGFUSE__ENABLED=1` 而本机 langfuse 容器停着，s15 跑到第九组卡住
    150 秒未出，`LANGFUSE__ENABLED=0` 同一份 30 秒跑完。

    两件必须同时做到的事，少一件就是假修：
      ① 整次 shutdown 放进 **daemon 线程**里等一个 grace，到点就走（SDK 自己的消费线程也是 daemon）；
      ② 顺手把 SDK 注册在 **atexit** 上的那个 `shutdown` 试着反注册掉（`resource_manager.py:279`
         `atexit.register(self.shutdown)`）——这一条**今天实测没做到**：包一层 `atexit.register` 看得清
         客户端建起来时挂了 4 个（`certifi.exit_cacert_ctx`、OTel 的 `TracerProvider.shutdown`、
         `PromptCacheTaskManager.shutdown`、`LangfuseResourceManager.shutdown`），而 `atexit.unregister`
         之后注册数一次都没降（09-24 实测）。留下这条尝试是因为它便宜且方向对，不是因为有用。
    ⚠ **所以本函数是有界「等待」，不是有界「耗时」**：放弃之后那份网络工作还在跑，进程退出时
    `concurrent.futures` 的收尾会 join 它的非 daemon 工作线程——实测尾巴 ≈ 一次无界 shutdown 的长度
    （40 个 span 量到 7.6s），且**不受 OTel 那两个超时环境变量约束**（`OTEL_BSP_EXPORT_TIMEOUT=1000`
    与 `=30000` 两档总墙钟一样 9.8s，09-24 实测 ⇒ 想靠调小 timeout 消掉尾巴是走不通的）。
    被修掉的真正症状是**每一次 lifespan 退出都等一轮**：s15 一份里六七个 TestClient ⇒ 改前逐个付
    （09-23 现取：整份 150 秒未出；改后 in-process 单次进出 1.01s，无界的旧形状同批 9.3s）。
    代价写在脸上并喊出来：grace 内没发完的那批 span 丢掉。这是本件**唯一**的降级形状，不静默。
    """
    global _client, _handler
    cli, _client, _handler = _client, None, None
    if cli is None or not enabled():
        return True                             # 没东西可冲 = 按时
    from codeharness.configs.settings import settings
    from codeharness.logs import logger
    grace = settings.langfuse.shutdown_grace_sec
    done = threading.Event()

    def _go():
        try:
            cli.shutdown()
        except Exception as e:                      # 端点挂了不该把停机路径抛出 traceback
            logger.debug(f"可观测 shutdown 抛错（不影响退出）: {type(e).__name__}: {e}")
        finally:
            done.set()

    threading.Thread(target=_go, daemon=True, name="langfuse-shutdown").start()
    if done.wait(grace):
        return True
    res = getattr(cli, "_resources", None)
    hook = getattr(res, "shutdown", None)
    if hook is not None:
        atexit.unregister(hook)                     # 见 docstring 的 ②：实测没做到，留着方向
    logger.warning(f"可观测停机只等 {grace}s：{settings.langfuse.host} 多半不可达，没发完的 span "
                   f"这批丢掉（要等久一点就调 LANGFUSE__SHUTDOWN_GRACE_SEC；不采就 LANGFUSE__ENABLED=0）")
    return False
