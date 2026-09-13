"""@exp_cache：语义对齐源 exp_pool/decorator.py:29 exp_cache——命中即复用，未命中执行后入库。"""
from functools import wraps
from codeharness.document_store.exp_store import ExpStore

_store = None


def _get_store():
    global _store
    if _store is None:
        _store = ExpStore()
    return _store


def exp_cache(action_tag: str, threshold: float = 0.9):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(self, msg, *a, **kw):
            store = _get_store()
            hit = await store.search(action_tag, msg.content[:512])
            if hit and hit.get("score", 0) >= threshold and hit.get("score", 0) <= 1.0:
                from codeharness.schema import Message
                return Message(content=f"[From Experience] {hit['output']}",
                               role="assistant", cause_by=action_tag)
            result = await fn(self, msg, *a, **kw)
            await store.save(action_tag, msg.content[:512], result.content)
            return result
        return wrapper
    return decorator
