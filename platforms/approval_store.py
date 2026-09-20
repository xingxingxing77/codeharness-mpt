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


def new_item(aid: str, tool: str, args_preview: str, reason: str,
             tier_required: str, tier_session: str, node: str = "") -> dict:
    return {"id": aid, "tool": tool, "args_preview": args_preview, "reason": reason,
            "tier_required": tier_required, "tier_session": tier_session, "node": node,
            "ts": time.time()}
