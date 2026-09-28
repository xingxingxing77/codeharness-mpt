"""待批登记（S11-批次36）：`HASH ch:appr:{sid}` = 待批项，`HASH ch:appr:{sid}:decisions` = 回执。

为什么是两个哈希而不是一个列表：gate 节点在 resume 后会被**整节点重放**，重放时它要按
`approval_id` 回读「这条已经批过了」——所以决策必须与待批项分开、且能单独命中。
`request` 用 HSETNX：同一动作重放不会把自己登记两次（也就不会在界面上冒出两张卡）。

写方是跑图的 worker，读方是 HTTP worker（`platforms/chat_queue.py` 同款跨进程前提）。
"""
import json
import time

import redis

from codeharness.configs.settings import RedisConfig, settings

KEY = "ch:appr:{}"
DECISIONS = ":decisions"
TTL_SEC = 30 * 86400            # 批过的痕迹留 30 天；没人处理的会话不该永久占内存


class ApprovalStore:
    def __init__(self, sid: str, config: RedisConfig | None = None):
        self.sid = sid                # 内核 gate 侧算 approval_id 要用；注入的就是这个对象
        self.key = KEY.format(sid)
        self.dkey = self.key + DECISIONS
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def request(self, item: dict) -> bool:
        """登记一条待批；返回是否新建（False = 重放命中，已问过）。"""
        created = self.r.hsetnx(self.key, item["id"], json.dumps(item, ensure_ascii=False))
        if created:
            self.r.expire(self.key, TTL_SEC)
            self.r.expire(self.dkey, TTL_SEC)
        return bool(created)

    def pending(self) -> list[dict]:
        raw = self.r.hgetall(self.key)
        decided = self.r.hgetall(self.dkey)
        return [json.loads(v) for k, v in sorted(raw.items(), key=lambda kv: _ts(kv[1]))
                if k not in decided]

    def decide(self, aid: str, outcome: str) -> str:
        """首个回执生效，后来的忽略（两个人同时点也只认第一个）。"""
        if self.r.hsetnx(self.dkey, aid, outcome):
            self.r.expire(self.dkey, TTL_SEC)
            # payload 留在 self.key 里：pending() 按决策哈希过滤，settled() 要拿它显示历史
            return outcome
        return self.r.hget(self.dkey, aid) or outcome

    def decision(self, aid: str) -> str | None:
        return self.r.hget(self.dkey, aid)

    def settled(self) -> list[dict]:
        raw = self.r.hgetall(self.key)
        return [{**json.loads(raw[aid]), "outcome": out}
                for aid, out in sorted(self.r.hgetall(self.dkey).items(), key=lambda kv: _ts(raw.get(kv[0], "{}")))
                if aid in raw]

    def item(self, aid: str) -> dict | None:
        v = self.r.hget(self.key, aid)
        return json.loads(v) if v else None


def _ts(raw: str) -> float:
    try:
        return float(json.loads(raw).get("ts", 0))
    except Exception:
        return 0.0


class InProcessApprovalStore:
    """无 Redis 档的审批台账（C87）：与 `ApprovalStore` **同形**（鸭子类型，不设共同基类）。

    用两个 dict 复刻那两个 HASH 的语义——`_items` = 待批（`ch:appr:{sid}`）、`_decisions` = 回执
    （`:decisions`）：`request` 的「id 已在 ⇒ False」复刻 HSETNX（gate 重放不会登记两次）、
    `decide` 的 `setdefault` 复刻「首个回执生效」。生命周期跟随进程（没有 TTL）：无 Redis 的
    部署本来就是单机单进程，条目随进程走，不会跨进程丢也不会无限涨。
    """

    def __init__(self, sid: str):
        self.sid = sid                # 内核 gate 侧要用；与 Redis 版同形
        self._items: dict[str, dict] = {}
        self._decisions: dict[str, str] = {}

    def request(self, item: dict) -> bool:
        """登记一条待批；返回是否新建（False = 重放命中，已问过）——HSETNX 语义。"""
        if item["id"] in self._items:
            return False
        self._items[item["id"]] = dict(item)
        return True

    def pending(self) -> list[dict]:
        return [self._items[k] for k in sorted(
            (k for k in self._items if k not in self._decisions),
            key=lambda k: self._items[k].get("ts", 0.0))]

    def decide(self, aid: str, outcome: str) -> str:
        """首个回执生效，后来的忽略（两个人同时点也只认第一个）——HSETNX 语义。"""
        return self._decisions.setdefault(aid, outcome)

    def decision(self, aid: str) -> str | None:
        return self._decisions.get(aid)

    def settled(self) -> list[dict]:
        return [{**self._items[aid], "outcome": out}
                for aid, out in sorted(self._decisions.items(),
                                       key=lambda kv: self._items.get(kv[0], {}).get("ts", 0.0))
                if aid in self._items]

    def item(self, aid: str) -> dict | None:
        return self._items.get(aid)


# C87（09-28 审查批）：台账有**两条读路**——跑图的 `_session_ctx`（写待批/读回执）与 HTTP 的
# approvals 路由（列待批/写回执）。Redis 档两路各建各的 `ApprovalStore` 没事（真源在 Redis）；
# 进程内档必须**同一个对象**，否则内核登记的卡 HTTP 永远看不见。所以给一个唯一出口 + 每会话
# 注册表，两条读路都改走它。模式**首次调用时定死**（与 app.py 启动时定 store/bus 的口径一致）：
# `use_redis` 且 ping 得通走 Redis 版；否则退进程内并留一句可 grep 的 warning。定了就不回头
# ——Redis 后来才起来的进程要重启才切（app.py 的 store/bus 同款口径）。
_MODE: str | None = None
_MEM: dict[str, InProcessApprovalStore] = {}


def ledger_for(sid: str):
    """按实际可达性挑台账实现；两条读路必须都走这个出口（理由见上）。"""
    global _MODE
    if _MODE is None:
        if settings.platform.use_redis:
            try:
                redis.Redis.from_url(settings.redis.to_url()).ping()
                _MODE = "redis"
            except Exception as e:
                from codeharness.logs import logger
                logger.warning(f"PLATFORM__USE_REDIS 已置位但 Redis 不可达——审批台账退回进程内实现: "
                               f"{type(e).__name__}: {e}")
        if _MODE is None:
            _MODE = "memory"
    if _MODE == "redis":
        return ApprovalStore(sid)
    return _MEM.setdefault(sid, InProcessApprovalStore(sid))


def new_item(aid: str, tool: str, args_preview: str, reason: str,
             tier_required: str, tier_session: str, node: str = "") -> dict:
    return {"id": aid, "tool": tool, "args_preview": args_preview, "reason": reason,
            "tier_required": tier_required, "tier_session": tier_session, "node": node,
            "ts": time.time()}
