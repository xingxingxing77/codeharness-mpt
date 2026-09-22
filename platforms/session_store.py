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
# 存进 Redis 哈希时一切都被 str() 过，这些字段要走 JSON；兜底值按类型给（列表字段
# 用 "{}" 会反序列化成 dict，pydantic 直接炸）。
_JSON_FIELDS = {"llm_override": {}, "cost": {}, "roles": [], "role_defs": [], "feedback": {}}


def _dump_fields(fields: dict) -> dict:
    """按 Redis 哈希的落库形态归一**任意字段子集**：JSON 字段先序列化，其余一律 str()。
    `update()` 只写传入字段靠它，`_dump` 全量落库也走它——两处不重复、不会漂移。"""
    d = dict(fields)
    for f in _JSON_FIELDS:
        if f in d:
            d[f] = json.dumps(d[f], ensure_ascii=False)
    return {k: str(v) for k, v in d.items()}


def _dump(s: Session) -> dict:
    # mode="json" 把枚举归一成 value 字符串（str(enum) 会打出 'SessionStatus.running'，_load 反序列化直接炸）
    return _dump_fields(s.model_dump(mode="json"))


def _load(sid: str, h: dict) -> Session:
    d = dict(h)
    d["id"] = d.get("id") or sid
    # 批次36 之前存的记录没有这个字段。缺字段≠新会话：老会话按改动前「无拦截」的行为读，
    # 否则恢复一个跑了一半的老会话会凭空每步弹审批。新建走模型默认（readonly）。
    if "permission" not in d:
        d["permission"] = "full_access"
    for f, empty in _JSON_FIELDS.items():
        d[f] = json.loads(d.get(f) or json.dumps(empty))
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
               paradigm: str = "classic", sop: str = "", user_id: str = "default",
               permission: str = "readonly", goal: str = "") -> Session:
        sid = uuid.uuid4().hex[:8]
        name = project_name or sid
        s = Session(id=sid, idea=idea, project_name=name, n_round=n_round,
                    paradigm=paradigm, sop=sop, user_id=user_id, permission=permission,  # B5：goal 走同一条 create 通路（建会话时就带上），默认空串=没有目标
                    goal=goal,
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
        # 与 JSON store 同一口径：先时间倒序，再按「未置顶」稳定排一次
        by_recency = sorted(out, key=lambda s: s.created_at, reverse=True)
        return sorted(by_recency, key=lambda s: not s.pinned)

    def delete(self, sid: str) -> bool:
        # 索引也要一起摘：只删哈希会让 list() 里留下一个 get 不到的幽灵条目
        removed = self.r.delete(KEY.format(sid))
        self.r.zrem(INDEX, sid)
        return bool(removed)

    def update(self, sid: str, persist: bool = True, **fields) -> Session:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        data = s.model_dump()
        data.update(fields)
        s = Session(**data)
        if persist:
            # 字段级 HSET：只写传进来的字段。全量重写=拿本次读到旧快照覆掉并发方刚写的字段
            # （cost 合流与 status 更新同刻必丢一边）；取值仍走重建后的模型，归一与 _dump 同源。
            changed = {k: v for k, v in s.model_dump(mode="json").items() if k in fields}
            self.r.hset(KEY.format(sid), mapping=_dump_fields(changed))
        return s

    def set_cost(self, sid: str, cost: dict, persist: bool = False):
        # Redis 即落盘：persist 参数只为与进程内实现同签名而存在
        self.r.hset(KEY.format(sid), "cost", json.dumps(cost, ensure_ascii=False))

    def heal_running(self):
        """服务重启自愈：**只**把 running 的残态改 stopped（C18①，与进程内实现同口径）。
        awaiting_human 不动：那是可信驻留态——卡仍在 ch:appr:{sid}（30 天 TTL）、断点在持久化 checkpointer 里，
        新进程按同一 thread_id 重建图就能续上（`runner._ensure_graph`），所以自愈无权替用户放弃这一场（C18① 实测：
        跨进程 respond 真把动作跑完）。多 worker 时只应在启动时跑一次（lifespan 装配处）。"""
        for sid in self.r.zrange(INDEX, 0, -1):
            st = self.r.hget(KEY.format(sid), "status")
            if st == SessionStatus.running.value:
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
