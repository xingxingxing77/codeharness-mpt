"""会话态 Redis 化（施工4 目标结构第 1 行）：`HASH ch:sess:{sid}` 字段级 HSET + `ZSET ch:index`。

消灭 `sessions.py:54` 的「每次 update 全量重写整份 JSON」——字段级更新天然并发安全，
两会话并发改各自字段不会互相覆盖。接口与 server.sessions.SessionStore 同名同签名，
runner/API 零改动即可换装（feature flag 双跑）。

同步客户端：store 调用是低频（每会话几次），且大量调用点在同步上下文
（runner._translate、API 的 def 端点）——异步化收益为零、桥接成本为真。
"""
import json
import time
import uuid
from pathlib import Path

import redis

from codeharness.configs.settings import RedisConfig, settings
from server.settings import WORKSPACE_ROOT
from server.sessions import Session, SessionStatus, _now

KEY = "ch:sess:{}"
INDEX = "ch:index"
_JSON_FIELDS = ("llm_override", "cost")


def _dump(s: Session) -> dict:
    d = s.model_dump(mode="json")     # 枚举归一成 value 字符串（str(enum) 会打出 'SessionStatus.created'）
    for f in _JSON_FIELDS:
        d[f] = json.dumps(d[f], ensure_ascii=False)
    return {k: str(v) for k, v in d.items()}


def _load(sid: str, h: dict) -> Session:
    d = dict(h)
    d["id"] = d.get("id") or sid
    for f in _JSON_FIELDS:
        d[f] = json.loads(d.get(f) or "{}")
    return Session(**d)


class RedisSessionStore:
    def __init__(self, config: RedisConfig | None = None):
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except Exception:
            return False

    def create(self, idea: str, n_round: int = 5,
               project_name: str = "", llm_override: dict | None = None,
               paradigm: str = "classic") -> Session:
        sid = uuid.uuid4().hex[:8]
        name = project_name or sid
        s = Session(id=sid, idea=idea, project_name=name, n_round=n_round,
                    paradigm=paradigm,
                    llm_override=llm_override or {},
                    workspace=str(WORKSPACE_ROOT / name), created_at=_now())
        pipe = self.r.pipeline()
        pipe.hset(KEY.format(sid), mapping=_dump(s))
        pipe.zadd(INDEX, {sid: time.time()})
        pipe.execute()
        return s

    def get(self, sid: str) -> Session | None:
        h = self.r.hgetall(KEY.format(sid))
        return _load(sid, h) if h else None

    def list(self) -> list:
        sids = self.r.zrevrange(INDEX, 0, -1)   # 同步客户端的 zrange/zrevrange 必须给区间（异步版有默认值，别拿它类推）
        if not sids:
            return []
        pipe = self.r.pipeline()
        for sid in sids:
            pipe.hgetall(KEY.format(sid))
        out = []
        for sid, h in zip(sids, pipe.execute()):
            if h:
                out.append(_load(sid, h))
        return sorted(out, key=lambda s: s.created_at, reverse=True)

    def update(self, sid: str, persist: bool = True, **fields) -> Session:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        data = s.model_dump()
        data.update(fields)
        s = Session(**data)
        if persist:
            # 字段级 HSET：只写传进来的那些字段（全量重写=并发覆盖的根因，这里也不做）
            self.r.hset(KEY.format(sid), mapping=_dump(s))
        return s

    def set_cost(self, sid: str, cost: dict, persist: bool = False):
        # Redis 即落盘：persist 参数只为与进程内实现同签名而存在
        self.r.hset(KEY.format(sid), "cost", json.dumps(cost, ensure_ascii=False))

    def heal_running(self):
        """服务重启自愈（与进程内实现同一语义）：running/awaiting_human 的残态改 stopped。
        多 worker 时只应在启动时跑一次（lifespan 装配处）。"""
        for sid in self.r.zrange(INDEX, 0, -1):
            st = self.r.hget(KEY.format(sid), "status")
            if st in (SessionStatus.running.value, SessionStatus.awaiting_human.value):
                self.r.hset(KEY.format(sid), "status", SessionStatus.stopped.value)

    def import_legacy(self, json_path: Path) -> int:
        """切默认一次性迁移：redis 索引还空、sessions.json 有货 → 原样搬进来。
        不搬的话前端会话列表在切换当天凭空清零——历史账本（含 cost 快照）是 S9 双跑的对照底料。"""
        if self.r.zcard(INDEX) or not json_path.exists():
            return 0
        n = 0
        for item in json.loads(json_path.read_text(encoding="utf-8")):
            sid = item["id"]
            s = Session(**item)
            pipe = self.r.pipeline()
            pipe.hset(KEY.format(sid), mapping=_dump(s))
            pipe.zadd(INDEX, {sid: n})          # 保序即可，list() 反正按 created_at 重排
            pipe.execute()
            n += 1
        return n
