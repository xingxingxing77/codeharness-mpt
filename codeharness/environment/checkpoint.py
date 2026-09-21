"""checkpointer 工厂（平台自有）。

分工：`build_team` 的默认值仍是内存 saver，保证内核自测不产生磁盘副作用；
**持久化由 server 侧显式注入**（与"拿不到就退回进程内实现"的同一条纪律）。

为什么必须持久化：`interrupt()` 之后图是暂停态，恢复靠 `thread_id` 找断点。
内存 saver 一重启就没了，人工回答只能命中同进程的那个 graph 实例。
"""
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from langgraph.checkpoint.memory import InMemorySaver

_lock = threading.Lock()
_cache: dict[str, object] = {}

# checkpointer 的 msgpack 白名单（README 未闭合 #4：不显式配，每读一次断点都打
# "Deserializing unregistered type codeharness.schema.Message"，官方声明未来版本将拦截）。
# 进过 TeamState 的自定义类型只有 Message 系；`Document`/`Documents` 两行原本是为
# `TeamState.docs` 那条假通道挂的（零读零写，产物实走磁盘 ArtifactStore），随 C2 删通道一并摘掉——
# 实测证据：摘掉后跑整套 s3b（含真落断点的 t7/t8）langgraph 侧 **0 条 unregistered 告警**。
# instruct_content 是纯 dict（schema.py:173），不需要逐个登记。
_ALLOWED = [("codeharness.schema", n) for n in
            ("Message", "UserMessage", "SystemMessage", "AIMessage")]


def _serde():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    return JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED)


def memory_saver():
    """内存 saver：自测与"存储后端不可用"时的兜底。"""
    return InMemorySaver(serde=_serde())


async def async_sqlite_saver(path: Path | str):
    """**异步**文件型 saver —— runner 全程走 `astream_events`，只有这个能用。

    为什么这个工厂必须是 `async def`：`AsyncSqliteSaver.__init__` 内部会调
    `asyncio.get_running_loop()`，没有运行中的事件循环时同步构造直接 `RuntimeError`
    （实测踩过，别改成同步"省事"）。

    ⚠ 也别用 `SqliteSaver`：它的 `aget_tuple`/`aput` 只是基类占位，一调就抛
    `NotImplementedError: The SqliteSaver does not support async methods`，
    而 `hasattr(...)` 会返回 True，别拿它当判断依据。

    按路径复用同一实例；返回 None 表示依赖缺失，调用方退回 memory_saver()。
    """
    key = str(Path(path))
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError:
            return None
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        saver = AsyncSqliteSaver(aiosqlite.connect(key), serde=_serde())
        _cache[key] = saver
        return saver


def sqlite_saver(path: Path | str):
    """同步文件型 saver：只给非异步调用方（批处理脚本等）用。"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        return None
    Path(str(path)).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：图跑在事件循环线程，saver 读写可能来自 executor 线程
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    saver = SqliteSaver(conn, serde=_serde())
    saver.setup()
    return saver


async def make_checkpointer(path: Optional[Path | str] = None, *, persist: bool = True):
    """统一入口（必须在事件循环内 await）：persist 且有可用路径时给异步文件型，否则内存型。"""
    if persist and path:
        saver = await async_sqlite_saver(path)
        if saver is not None:
            return saver
    return memory_saver()


def default_checkpoint_path(workspace_root: Path | str) -> Path:
    """默认落在 workspace 下的 storage/，与产物同生命周期，删会话即一起清掉。"""
    return Path(workspace_root) / "storage" / "checkpoints.db"


async def close_all():
    """关闭并清空缓存的连接。

    必须显式关：aiosqlite 的后台线程在解释器退出时若事件循环已关闭，会抛
    `RuntimeError: Event loop is closed`——实测让自测**断言全过但退出码为 1**，
    这种门禁等于没有门禁。server 应在 lifespan 关闭时调用它，自测在收尾时调用。"""
    with _lock:
        items = list(_cache.items())
        _cache.clear()
    for key, saver in items:
        conn = getattr(saver, "conn", None)
        close = getattr(conn, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:                               # 停机阶段的失败不该阻断其余连接关闭
            pass
