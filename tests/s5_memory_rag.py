"""S5.1 门禁：Redis 薄壳、BrainMemory 摘要与落盘、RoleZero 工作记忆回喂。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s5_memory_rag.py

断言打在哪（docs 陷阱 #2 要求自证）：
  - t1/t3/t5/t9/t10 打真 Redis（127.0.0.1:6379），不是 mock；
  - t2 把 settings.redis 指向死端口，验的是「连不上不抛」这条降级语义；
  - t7/t8/t11 打在 FakeLLM 收到的 payload 上——要钉的就是「发给模型的消息里有没有上一轮结果」，
    所以记账对象是 messages 列表本身，不是模型返回值。
  - 全程零外网、零真模型。Redis 没起时本文件会红，那是真的：t1/t3 依赖它，
    而 t2 保证产品路径在没 Redis 的机器上只是不摘要、不炸。
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import redis as sync_redis

from codeharness.configs.settings import settings
from codeharness.memory.brain_memory import BrainMemory
from codeharness.runtime import CURRENT_PROJECT
from codeharness.schema import Message
from codeharness.utils.redis import Redis

DEAD_PORT = 6399            # 未监听端口：验降级用
KEYS: list[str] = []        # 本文件写进 Redis 的 key，收尾统一删


def fresh_key(prefix="s5gate"):
    k = f"{prefix}:{uuid.uuid4().hex[:8]}"
    KEYS.append(k)
    return k


def live_redis() -> bool:
    try:
        return sync_redis.Redis(host=settings.redis.host, port=settings.redis.port,
                                db=settings.redis.db).ping()
    except Exception:
        return False


class FakeLLM:
    """记账型假模型：把每次收到的 messages 全存下来，摘要固定回一段文本。"""

    def __init__(self, summary="已压缩的历史摘要"):
        self.payloads: list[list] = []
        self.summary = summary

    async def aask(self, msg, system_msgs=None, stream=False, tag="", **kw) -> str:
        self.payloads.append(list(msg) if isinstance(msg, list) else [msg])
        return self.summary

    def structured(self, cls):
        outer = self

        class _Bound:
            async def ainvoke(self, msgs, **kw):
                outer.payloads.append(list(msgs))
                return cls(thought="先写文件", commands=[])

        return _Bound()


# ---------------- Redis 薄壳 ----------------
def t1_redis_roundtrip_and_expiry():
    async def go():
        k = fresh_key()
        r = Redis()
        assert await r.set(key=k, data="v1", timeout_sec=30) is True
        assert await r.get(key=k) == b"v1"
        assert await r.get(key="") is None           # 空 key 直接短路，不发请求
        await r.close()
    asyncio.run(go())
    print("  t1 Redis 往返 + TTL + 空 key 短路")


def t2_redis_down_degrades_to_none():
    """降级语义是硬要求：连不上只 warning，读写返回 None/False，绝不抛。"""
    keep = settings.redis.port
    settings.redis.port = DEAD_PORT
    try:
        async def go():
            r = Redis()
            assert await r.get(key=fresh_key()) is None
            assert await r.set(key=fresh_key(), data="x") is False
            await r.close()                         # 没连上也要能关
        asyncio.run(go())
    finally:
        settings.redis.port = keep
    print("  t2 无 Redis：读写静默降级，不抛")


# ---------------- BrainMemory ----------------
def t3_brain_dumps_loads_only_when_dirty():
    keep = settings.redis.port
    if not live_redis():
        print("  t3 跳过（无 Redis）")
        return
    async def go():
        k = fresh_key("brain")
        b = BrainMemory()
        assert await b.dumps(redis_key=k) is False   # 干净时不写盘
        b.add_talk(Message(content="第一轮问话", sent_from="user"))
        b.add_answer(Message(content="第一轮回答", sent_from="Alice"))
        assert await b.dumps(redis_key=k, timeout_sec=30) is True
        assert b.is_dirty is False
        again = await BrainMemory().loads(redis_key=k)
        assert [m.content for m in again.history] == ["第一轮问话", "第一轮回答"]
        assert again.is_dirty is False and again.last_history_id == b.last_history_id
        assert await again.dumps(redis_key=k) is False      # loads 回来的实例不该立刻回写
    try:
        asyncio.run(go())
    finally:
        settings.redis.port = keep
    print("  t3 BrainMemory 整体 JSON 存单 key，dirty 才写")


def t4_overflow_uses_memory_overflow_size():
    keep = settings.memory_overflow_size
    settings.memory_overflow_size = 2
    try:
        b = BrainMemory()
        assert not b.is_overflow()
        for i in range(3):
            b.add_history(Message(content=f"m{i}"))
        assert b.is_overflow()
    finally:
        settings.memory_overflow_size = keep
    print("  t4 溢出判定读 settings.memory_overflow_size（该字段此前的零读者已闭合）")


def t5_summarize_rolls_history_into_summary_and_persists():
    if not live_redis():
        print("  t5 跳过（无 Redis）")
        return
    k = fresh_key("brain")
    b = BrainMemory()
    for i in range(6):
        b.add_history(Message(content=f"第{i}轮：" + "很长的一段对话内容" * 30))
    fake = FakeLLM(summary="六轮压缩成一句")
    got = asyncio.run(b.summarize(fake, redis_key=k, max_words=200))
    # 超一个窗口(1500) → 分两窗各摘一次再合并，收敛后拿到的是合并串（源同款行为）
    assert got.count(fake.summary) == 2, got
    assert b.history == [] and b.historical_summary == got
    raw = sync_redis.Redis(host=settings.redis.host, port=settings.redis.port,
                           db=settings.redis.db).get(k)   # db 跟 settings 走：写侧（utils/redis）用的就是它
    assert json.loads(raw)["historical_summary"] == got      # 存的是整体 JSON，按字段回读
    # 单窗路径：文本装得进一个窗口(1500)但超 max_words → 一次直摘，返回值即模型原文
    short = BrainMemory()
    short.add_history(Message(content="一" * 300))
    assert asyncio.run(short.summarize(FakeLLM(summary="短摘要"), redis_key="", max_words=200)) == "短摘要"
    print("  t5 摘要后 history 清空、historical_summary 落 Redis；分窗合并与单窗直摘两条都验")


def t6_split_texts_overlaps_and_multiwindow_reduces():
    windows = BrainMemory.split_texts("x" * 3000, window_size=100)
    assert len(windows) > 20 and all(len(w) <= 100 for w in windows)
    assert windows[1].startswith("x" * 80)          # 重叠 = window_size - padding(20)
    assert BrainMemory.split_texts("short", window_size=100) == ["short"]
    # 多窗合并：每摘一次文本变短，循环到装进单窗
    fake = FakeLLM(summary="s" * 50)
    b = BrainMemory()
    b.add_history(Message(content="y" * 3000))
    merged = asyncio.run(b.summarize(fake, redis_key="", max_words=200))
    assert len(merged) < 3000 and "s" * 50 in merged and b.historical_summary == merged
    print("  t6 分窗带重叠；多窗摘要合并后收敛")


# ---------------- RoleZero 工作记忆 ----------------
def _role(brain=None, memory_k=50):
    from codeharness.roles.role_zero import RoleZero
    return RoleZero({"name": "Alice", "profile": "Product Manager", "goal": "g"},
                    [], FakeLLM(), memory_k=memory_k, brain=brain)


def t7_tool_results_feed_next_round_prompt():
    """本轮抓出的真实缺陷：_act 的结果此前从不回喂，CMD_PROMPT 却要求 review the history。"""
    role = _role()
    s0 = {"task": "写个 prd", "history": [], "experience": "", "respond_language": "中文", "finished": False}
    r1 = asyncio.run(role._think(s0))                         # 图是**合并**语义：下一轮状态 = 起跑状态 + 节点返回
    assert len(role.memory.storage) == 1                       # 只有 thought 入库
    acted = {**s0, **r1, "history": [{**r1["history"][-1],
                                "results": [{"name": "write_file", "result": "已写入 prd.md"}]}]}
    asyncio.run(role._think(acted))
    last = role.llm.payloads[-1]
    assert any("已写入 prd.md" in str(m.content) for m in last), "上一轮工具结果没进下一轮 prompt"
    assert len(last) > 2, "消息数没长大，说明只发了 System+Human 两条"
    print("  t7 上一轮工具结果确实出现在下一轮发给模型的 messages 里")


def t8_no_brain_windows_at_prompt_time():
    role = _role(brain=None, memory_k=2)
    for i in range(5):
        role.memory.add(Message(content=f"r{i}结果", role="user"))
    asyncio.run(role._compress())
    assert len(role.memory.storage) == 5                       # 无 brain：不动 storage，也没什么可摘要
    assert [m.content for m in role._context_messages()] == ["r3结果", "r4结果"]
    print("  t8 无 brain：只在组 prompt 时截窗，不动 storage、不抛不摘要")


def t9_brain_overflow_summarizes_and_restores():
    """溢出 → 摘要 → 落 Redis → 新实例 loads 回来，整条闭环一次跑完。"""
    if not live_redis():
        print("  t9 跳过（无 Redis）")
        return
    CURRENT_PROJECT.set("s5_mem_session")
    role = _role(brain=BrainMemory(), memory_k=2)
    role.llm = FakeLLM(summary="窗口外三轮的摘要")
    for i in range(5):
        role.memory.add(Message(content=f"c{i}命令结果" + "长" * 100))
    asyncio.run(role._compress())
    assert len(role.llm.payloads) == 1                          # 短于 max_words 的文本源是直返不调模型
    assert role.brain.historical_summary == "窗口外三轮的摘要"
    assert len(role.memory.storage) == 2
    ctx = role._context_messages()
    assert "窗口外三轮的摘要" in str(ctx[0].content) and len(ctx) == 3   # 摘要 + 窗口两条
    restored = asyncio.run(BrainMemory().loads(role._brain_key()))
    assert restored.historical_summary == "窗口外三轮的摘要"
    print("  t9 溢出摘要落 Redis，新 BrainMemory 按 key 恢复")


def t10_memory_keys_are_per_role_and_per_session():
    CURRENT_PROJECT.set("s5_mem_session")
    a, b = _role(), _role()
    b.profile = {"name": "Team Leader", "profile": "Team Leader", "goal": "g"}
    assert a._brain_key() != b._brain_key()
    assert a._brain_key().endswith("/Alice") and "s5_mem_session" in a._brain_key()
    CURRENT_PROJECT.set("s5_other_session")
    assert "s5_other_session" in a._brain_key()               # 换会话就是换 key，不互串
    print("  t10 记忆 key 按会话+角色分开（CURRENT_PROJECT 同一接缝）")


def t11_observe_dedupes_and_survives_partial_results():
    """多命令轮里 results 可能少于 commands（超时/异常分支），同一条结果不能被记两次。"""
    role = _role()
    st = {"task": "t", "history": [{"thought": "x", "commands": [],
                                     "results": [{"name": "shell", "result": "同一句输出"}]}],
          "experience": "", "respond_language": "中文", "finished": False}
    role._observe(st)
    role._observe(st)
    assert role.memory.count() == 1
    empty = {"task": "t", "history": [{"thought": "x", "commands": []}],
             "experience": "", "respond_language": "中文", "finished": False}
    role._observe(empty)                                      # 还没执行的轮：无 results 不报错
    assert role.memory.count() == 1
    got = asyncio.run(role._think({**empty, "history": []}))["history"][0]
    assert got["commands"] == [{"command_name": "end", "args": {}}]   # 空命令兜底成 end 的契约没被记忆改动
    print("  t11 结果去重、无 results 的轮不报错")


# ---------------- R9 · Qdrant named vectors / hybrid / 多租户 ----------------
GATE_COLL = "s5gate"            # 自测专用集合，绝不碰生产 collection


def live_qdrant() -> bool:
    import httpx
    from codeharness.configs.settings import settings
    try:
        return httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception:
        return False


def gate_store():
    from codeharness.document_store.qdrant_store import QdrantStore
    return QdrantStore(collection=GATE_COLL)


def pid(tag: str) -> str:
    """Qdrant 点 id 只收无符号整数或 UUID，自测里的可读名字统一派生成 UUID。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, tag))


# 替身本体在 `codeharness/provider/fake.py`（C3 起 S15 的知识库门禁共用它，不再各存一份）
from codeharness.provider.fake import (DirEmbeddings, HashEmbeddings,   # noqa: E402
                                       uncalibrated_embeddings)


class BoomEmbeddings(HashEmbeddings):
    async def aembed_documents(self, texts):
        raise ConnectionError("embedding 端点不在线")

    async def aembed_query(self, q):
        raise ConnectionError("embedding 端点不在线")


def t12_collection_shape():
    """R9 的四条形态必须在真集合上看得见，不是配置字符串。"""
    if not live_qdrant():
        print("  t12 跳过（无 Qdrant）")
        return
    from qdrant_client import QdrantClient
    st = gate_store()
    asyncio.run(st.ensure(HashEmbeddings().dim))
    c = QdrantClient(url=settings.qdrant.url)
    info = c.get_collection(GATE_COLL)
    assert "dense" in str(info.config.params.vectors), info.config.params.vectors
    assert "sparse" in str(info.config.params.sparse_vectors)
    assert "int8" in str(info.config.quantization_config).lower(), "标量量化没生效"
    schema = info.payload_schema
    assert "user_id" in schema and "doc_type" in schema, list(schema)
    assert schema["user_id"].params.is_tenant is True, "user_id 没建成租户索引"
    assert not schema["doc_type"].params.is_tenant, "doc_type 不该是租户索引"
    print("  t12 集合形态：named dense+sparse(IDF)+INT8 量化+user_id 租户索引")


def t13_tenant_and_doctype_isolation():
    if not live_qdrant():
        print("  t13 跳过（无 Qdrant）")
        return
    from codeharness.document_store.qdrant_store import Point
    st = gate_store()
    emb = HashEmbeddings()
    asyncio.run(st.write([
        Point(id=pid("u1-kb"), text="alpha 租户的 kb 内容 zzz", dense=emb._v("alpha 租户的 kb 内容 zzz"),
              doc_type="kb", user_id="u1"),
        Point(id=pid("u2-kb"), text="alpha 租户的 kb 内容 zzz", dense=emb._v("alpha 租户的 kb 内容 zzz"),
              doc_type="kb", user_id="u2"),
        Point(id=pid("u1-mem"), text="alpha 租户的 kb 内容 zzz", dense=emb._v("alpha 租户的 kb 内容 zzz"),
              doc_type="memory", user_id="u1")]))
    q = emb._v("alpha 租户的 kb 内容 zzz")
    ids = lambda hits: {h.id for h in hits}
    assert ids(asyncio.run(st.search("alpha zzz", q, user_id="u1", doc_type="kb"))) == {pid("u1-kb")}
    assert ids(asyncio.run(st.search("alpha zzz", q, user_id="u1"))) == {pid("u1-kb"), pid("u1-mem")}
    assert ids(asyncio.run(st.search("alpha zzz", q, user_id="u2", doc_type="memory"))) == set()
    print("  t13 单集合内 user_id 与 doc_type 双向隔离（跨租户/跨切片都查不到）")


def t14_sparse_indices_are_process_stable():
    """维度必须跨进程稳定：`str.hash()` 按进程加盐，用它做 sparse 下标 = 索引随重启失效。"""
    import json
    import subprocess
    from codeharness.document_store.qdrant_store import sparse_from_text
    text = "session_root 会话隔离 123"
    a = sparse_from_text(text)
    code = ("import json;from codeharness.document_store.qdrant_store import sparse_from_text as s;"
            f"print(json.dumps(s({text!r}).indices))")
    env = {**os.environ, "PYTHONHASHSEED": "12345",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, timeout=90).stdout.strip()
    assert out and json.loads(out) == list(a.indices), f"换进程加盐后维度变了：{out} vs {a.indices}"
    print("  t14 sparse 下标用 crc32，跨进程（换 PYTHONHASHSEED）稳定")


def t15_longterm_overflow_recall_roundtrip():
    if not live_qdrant():
        print("  t15 跳过（无 Qdrant）")
        return
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.schema import Message
    CURRENT_PROJECT.set("s5_r9_proj")
    ltm = LongTermMemory(embeddings=HashEmbeddings(), user_id="u_r9", store=gate_store())
    n = asyncio.run(ltm.overflow([Message(content="run_proc 在 Windows 上按进程树杀子进程",
                                         role="user", sent_from="Alex"),
                                  Message(content="同上", role="user")]))
    assert n == 2 and asyncio.run(ltm.overflow([Message(content="run_proc 在 Windows 上按进程树杀子进程",
                                                        role="user", sent_from="Alex")])) == 1
    with uncalibrated_embeddings():     # 本格钉的是入库幂等与召回字段，不是 C23 那根线（替身刻度未标定）
        hits = asyncio.run(ltm.recall("run_proc 超时怎么保留输出", k=3))
    assert any("进程树" in h.content for h in hits), hits
    one = next(h for h in hits if "进程树" in h.content)
    assert one.sent_from == "Alex" and one.role == "user"
    asyncio.run(ltm.drop())
    assert asyncio.run(ltm.recall("run_proc", k=3)) == []
    print("  t15 LongTermMemory 入库幂等 + 召回字段完整 + drop 收口")


def t16_rolezero_uses_longterm_recall():
    """role_zero.py:49 的 self.ltm 此前从不被构造，recall 分支是死代码。"""
    if not live_qdrant():
        print("  t16 跳过（无 Qdrant）")
        return
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.schema import Message
    CURRENT_PROJECT.set("s5_r9_proj")
    ltm = LongTermMemory(embeddings=HashEmbeddings(), user_id="u_r9", store=gate_store())
    asyncio.run(ltm.overflow([Message(content="既有约定：门禁一律不花钱", role="user", sent_from="Memo")]))
    role = _role()
    role.ltm = ltm
    with uncalibrated_embeddings():     # 同上：这一格钉「召回的经验真出现在发给模型的 prompt 里」
        asyncio.run(role._think({"task": "给项目加个门禁", "history": [], "experience": "",
                                 "respond_language": "中文", "finished": False}))
    joined = " ".join(str(getattr(m, "content", m)) for m in role.llm.payloads[-1])
    assert "门禁一律不花钱" in joined, "召回的经验没进 prompt"
    asyncio.run(ltm.drop())
    print("  t16 召回的经验真出现在发给模型的 prompt 里")


def t17_embedding_outage_degrades_not_crashes():
    """embedding 端点下线是运行态，不是编程错误：只降级 + 留痕，不能把角色跑死。"""
    if not live_qdrant():
        print("  t17 跳过（无 Qdrant）")
        return
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.schema import Message
    CURRENT_PROJECT.set("s5_r9_boom")
    role = _role(memory_k=1)
    role.ltm = LongTermMemory(embeddings=BoomEmbeddings(), user_id="u_boom", store=gate_store())
    assert asyncio.run(role._ltm_recall("任何任务")) == ""
    role.memory.add(Message(content="第一条", role="user"))
    role.memory.add(Message(content="第二条", role="user"))
    asyncio.run(role._compress())                   # 入库失败 → warning，不抛
    assert role.memory.count() == 1
    print("  t17 embedding 不可用：召回与入库都只降级不抛")


CORPUS_QUERY = [  # (标识符, query)：gold 与干扰项共用同一段中文尾，只差那个标识符的拼写
    ("run_proc", "run_proc 的超时行为是怎么处理的"),
    ("session_root", "session_root 返回的是哪个目录"),
    ("is_relative_to", "判越界为什么要用 is_relative_to"),
    ("stream_usage", "流式调用为什么要开 stream_usage"),
]
TAIL = "这一条说明写在本仓的施工文档里，涉及超时、目录、日志与重试的默认行为，生产环境请按约定办。"


def near_miss(key: str) -> list[str]:
    """五种变异都保证「词形不同」：大小写变体与「gold 的超集」不能用，它们在词法上等价，会把对照稀释掉。
    字符集却高度重合（反转、去下划线、换尾缀）—— 正是字符袋 dense 分不开、分词 sparse 分得开的那一类。"""
    return [key[:-1], key.replace("_", ""), key[:-2] + "ive", key[::-1], key.replace("_", "x")]


def t18_hitrate_single_vs_hybrid_table():
    """S5 唯一的升级度量：同一批 query，dense-only 与 hybrid(dense+sparse) 的 hit-rate@5，两份都存盘。

    没有这张表，S5 就只是"改写了"，不是"升级了"（docs §S5 门禁原话）。
    对照靠的是假 dense 的真实缺陷：bag-of-chars 只有字符重叠、没有词边界，
    近似拼写的干扰项一批量塞进语料就把 gold 挤出 top-5；词法 sparse 有词边界 + 服务端 IDF，仍能置顶。
    真 bge-m3 上线后要拿同一份 query 集重测一遍（那张表才是 S9 的基线）。
    """
    if not live_qdrant():
        print("  t18 跳过（无 Qdrant）")
        return
    import json
    from codeharness.document_store.qdrant_store import Point
    st = gate_store()
    emb = HashEmbeddings()
    docs = []                                    # (text, 是否 gold, gold key)
    for key, _ in CORPUS_QUERY:
        docs.append((f"{TAIL} 关键实现见 {key}。", True, key))
        docs += [(f"{TAIL} 相关实现见 {w}。", False, key) for w in near_miss(key)]
    asyncio.run(st.write([Point(id=pid(f"bench{i}"), text=t, dense=emb._v(t), doc_type="kb",
                                user_id="u_bench") for i, (t, _, _) in enumerate(docs)]))
    n, rows = len(CORPUS_QUERY), []
    ranks = {"dense_only": [], "hybrid": []}
    for key, query in CORPUS_QUERY:
        want = next(pid(f"bench{i}") for i, (t, gold, g) in enumerate(docs) if gold and g == key)
        got = {}
        for mode, hyb in (("dense_only", False), ("hybrid", True)):
            ids = [h.id for h in asyncio.run(st.search(query, emb._v(query), k=5, hybrid=hyb,
                                                       doc_type="kb", user_id="u_bench"))]
            got[mode] = ids.index(want) + 1 if want in ids else None
            ranks[mode].append(got[mode])
        rows.append({"query": query, "gold": key, "rank_dense_only": got["dense_only"],
                    "rank_hybrid": got["hybrid"]})

    def hit(rs, k):
        return sum(1 for r in rs if r is not None and r <= k)

    def mean(rs):
        v = [r for r in rs if r is not None]
        return round(sum(v) / len(v), 3) if v else None

    at = {f"{m}_hit@{k}": hit(ranks[m], k) for m in ranks for k in (1, 3, 5)}
    out = Path(settings.workspace_root).parent / "storage" / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    table = {"metric": "hit-rate@k + mean rank", "corpus": len(docs), "queries": n,
             "dense_only": {"hit@1": at["dense_only_hit@1"] / n, "hit@3": at["dense_only_hit@3"] / n,
                            "hit@5": at["dense_only_hit@5"] / n, "mean_rank": mean(ranks["dense_only"])},
             "hybrid": {"hit@1": at["hybrid_hit@1"] / n, "hit@3": at["hybrid_hit@3"] / n,
                        "hit@5": at["hybrid_hit@5"] / n, "mean_rank": mean(ranks["hybrid"])},
             "embedding": "hash-fake(64d, bag-of-chars)", "sparse": "crc32 token tf + 服务端 IDF",
             "note": "语料 24 条时 hit@5 两边都饱和，差额只出现在 @1/@3 与平均名次；"
                     "假 dense 无词边界，真 bge-m3 上线后要拿同一份 query 集重测",
             "rows": rows}
    (out / "s5_hitrate.json").write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    asyncio.run(st.delete_scope(doc_type="kb", user_id="u_bench"))
    d, h = table["dense_only"], table["hybrid"]
    assert h["hit@5"] >= d["hit@5"], f"hybrid 的 hit@5 退步了：{table}"
    assert h["hit@1"] > d["hit@1"] and h["mean_rank"] < d["mean_rank"], \
        f"hybrid 没把 gold 顶到更前，融合这一路没起作用：{table}"
    print(f"  t18 hit@1 {at['dense_only_hit@1']}/{n}→{at['hybrid_hit@1']}/{n}，"
          f"hit@5 {at['dense_only_hit@5']}/{n}→{at['hybrid_hit@5']}/{n}，"
          f"mean rank {d['mean_rank']}→{h['mean_rank']}；表已存 storage/benchmark/s5_hitrate.json")


# ---------------- S5.3 · 经验池闭环（schema 往返 / 命中计数排序 / @exp_cache 真接线） ----------------


def t19_exp_schema_roundtrip():
    """判定表 S5.3 门禁第一条：序列化 roundtrip 逐字段相等（格式照源，S9 双跑要吃这份 JSON）。"""
    from codeharness.exp_pool.schema import (EntryType, Experience, ExperienceType, Metric,
                                             Score, Trajectory)
    e = Experience(req="做个2048", resp='{"thought":"先建文件","commands":[]}',
                   metric=Metric(time_cost=1.5, money_cost=0.0, score=Score(val=7, reason="好")),
                   exp_type=ExperienceType.INSIGHT, entry_type=EntryType.MANUAL,
                   tag="RoleZero.llm_cached_think",
                   traj=Trajectory(plan="p", action="a", observation="o", reward=1))
    back = Experience.model_validate_json(e.model_dump_json())
    assert back == e and back.model_dump() == e.model_dump(), "roundtrip 不等，格式漂移了"
    assert set(e.model_dump()) == {"req", "resp", "metric", "exp_type", "entry_type",
                                   "tag", "traj", "timestamp", "uuid"}, e.model_dump().keys()
    assert back.rag_key() == "做个2048"
    print("  t19 Experience JSON roundtrip 逐字段相等，9 个字段名与源一致")


def t20_serializer_think_roundtrip():
    """被缓存的载荷（ZeroThought JSON）无损往返；键=最后一条 human（CMD_PROMPT），system 不进键。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    from codeharness.exp_pool.serializers import RoleZeroSerializer
    from codeharness.roles.role_zero import ZeroThought
    ser = RoleZeroSerializer()
    req = [SystemMessage(content="很大一段人设，不该进键"),
           HumanMessage(content="step 3/15 当前任务：main.py")]
    key = ser.serialize_req(req=req)
    assert key == "step 3/15 当前任务：main.py", key
    t = ZeroThought(thought="先写 main.py", commands=[{"command_name": "end", "args": {}}])
    dumped = ser.serialize_resp(t.model_dump_json())
    back = ZeroThought.model_validate_json(ser.deserialize_resp(dumped))
    assert back.model_dump() == t.model_dump(), "ZeroThought 过经验池不无损"
    print("  t20 裁剪键正确，ZeroThought 序列化往返逐字段相等")


class StubStore:
    """manager 的替身存储：给定 (tag, req)→resp，search 按预设相似度出候选。"""

    def __init__(self, rows: list[dict]):
        self.rows = rows          # [{"id","action_tag","input","output","score"}]

    async def search(self, action_tag, query, k=2):
        return [r for r in self.rows if r["action_tag"] == action_tag][:k]


class StubCounter:
    def __init__(self, counts: dict):
        self.counts = counts

    async def get(self, exp_id):
        return self.counts.get(exp_id, 0)

    async def bump(self, exp_id):
        self.counts[exp_id] = self.counts.get(exp_id, 0) + 1


def t21_hit_count_reorders():
    """判定表 S5.3 门禁第二条：命中计数改变排序——计数高的排前面，计数打平才轮到相似度。
    点 id 用真实的派生式（record_hit 按 (tag,req) 反推同一个 id，这里必须一致）。"""
    from codeharness.document_store.exp_store import exp_point_id
    from codeharness.exp_pool.manager import ExperienceManager
    hi, lo = exp_point_id("T", "近问题", "u21"), exp_point_id("T", "远问题", "u21")
    rows = [{"id": hi, "action_tag": "T", "input": "近问题", "output": "A", "score": 0.99},
            {"id": lo, "action_tag": "T", "input": "远问题", "output": "B", "score": 0.85}]
    counts = {lo: 3}                                     # 低相似但被复用 3 次的那条
    # user_id 必须与上面 hi/lo 的派生同一个租户：C4 之后经验 id 带租户，
    # record_hit 反推的键只有同租户才对得上（不同租户本来就是两条不同的经验）
    mgr = ExperienceManager(store=StubStore(rows), counter=StubCounter(counts), user_id="u21")
    got = asyncio.run(mgr.query_exps("q", tag="T"))
    assert [e.resp for e, _ in got] == ["B", "A"], "命中计数没有改变排序"
    counts[hi] = 3                                       # 追平 → 相似度做 tie-break
    got = asyncio.run(mgr.query_exps("q", tag="T"))
    assert [x.resp for x, _ in got] == ["A", "B"], "计数打平时相似度必须做第二键"
    from codeharness.exp_pool.schema import Experience
    asyncio.run(mgr.record_hit(Experience(req="近问题", resp="A", tag="T")))
    assert counts[hi] == 4, "record_hit 没走 bump"
    print("  t21 命中计数改变排序（计数>相似度 两级键），record_hit 只对复用的那条计数")


def t22_exp_cache_semantics():
    """@exp_cache 的开关矩阵：disabled 透传；read 命中跳过函数并计数；低于阈值照常执行；
    write 才入库；read 抛异常只 warning 不断主流程；缺 req 报 ValueError（源契约）。"""
    from codeharness.configs.settings import settings
    from codeharness.exp_pool.decorator import exp_cache
    from codeharness.exp_pool.schema import Experience, QueryType

    calls = {"n": 0}

    class FakeMgr:
        def __init__(self):
            self.saved, self.hits = [], []
            self.next_score = 0.99

        async def query_exps(self, req, tag="", query_type=QueryType.SEMANTIC, k=2):
            if tag not in [x.tag for x in self.saved]:
                return []
            return [(self.saved[0], self.next_score)]

        async def create_exp(self, exp):
            self.saved.append(exp)

        async def record_hit(self, exp):
            self.hits.append(exp)

    @exp_cache(manager=None, serializer=None)
    async def ask(*, req):
        calls["n"] += 1
        return f"answer::{req}"

    saved, settings.exp_pool = settings.exp_pool, settings.exp_pool.model_copy()
    try:
        settings.exp_pool.enabled = False
        assert asyncio.run(ask(req="q1")) == "answer::q1" and calls["n"] == 1
        assert asyncio.run(ask(req="q1")) == "answer::q1" and calls["n"] == 2   # 关着：透传，不查池
        mgr = FakeMgr()
        import codeharness.exp_pool.manager as mg
        mg._managers["default"] = mgr
        settings.exp_pool.enabled = True
        settings.exp_pool.enable_read = settings.exp_pool.enable_write = True
        assert asyncio.run(ask(req="q2")) == "answer::q2"          # miss：执行并入库
        assert len(mgr.saved) == 1 and mgr.saved[0].tag == "ask"   # 裸函数 tag=函数名（源 _generate_tag）
        assert asyncio.run(ask(req="q2")) == "answer::q2" and calls["n"] == 3
        assert mgr.hits and mgr.hits[0] is mgr.saved[0]            # 命中：函数没再执行，计数记了
        mgr.next_score = 0.5                                       # 低于 0.9 阈值 → 不复用
        assert asyncio.run(ask(req="q2")) == "answer::q2" and calls["n"] == 4

        class BoomMgr(FakeMgr):
            async def query_exps(self, *a, **kw):
                raise ConnectionError("qdrant down")

        mgr2 = BoomMgr()
        mgr2.saved = [Experience(req="q3", resp="answer::q3", tag="ask")]
        mg._managers["default"] = mgr2
        assert asyncio.run(ask(req="q3")) == "answer::q3" and calls["n"] == 5   # 读挂：照常执行
        try:
            asyncio.run(ask("positional"))
            raise AssertionError("缺 req 必须 ValueError")
        except ValueError:
            pass
    finally:
        settings.exp_pool = saved
        mg._managers.pop("default", None)
    print("  t22 @exp_cache：透传/命中跳LLM/阈值把关/计数/读挂降级/req 契约 六条全过")


def t23_exp_store_replay_on_qdrant():
    """真 Qdrant（gate 集合）+ hash-fake embedding 的存取回放：同 tag 命中、跨 tag 不漏。"""
    if not live_qdrant():
        print("  t23 跳过（无 Qdrant）")
        return
    from codeharness.document_store.exp_store import ExpStore, exp_point_id
    from codeharness.exp_pool.manager import ExperienceManager, HitCounter
    from codeharness.exp_pool.schema import Experience

    user = f"u_exp_{uuid.uuid4().hex[:6]}"
    store = ExpStore(embeddings=HashEmbeddings(), user_id=user, store=gate_store())
    counter = HitCounter(user_id=user)
    KEYS.append(f"exp_hits:{user}:{exp_point_id('RoleZero.llm_cached_think', '做个2048', user)}")
    mgr = ExperienceManager(store=store, counter=counter)
    exp = Experience(req="做个2048", resp='{"thought":"先建文件","commands":[]}',
                     tag="RoleZero.llm_cached_think")
    asyncio.run(mgr.create_exp(exp))
    got = asyncio.run(mgr.query_exps("做个2048", tag=exp.tag))
    assert got and got[0][0].resp == exp.resp and got[0][1] >= 0.99, got
    assert asyncio.run(mgr.query_exps("做个2048", tag="Other.action")) == []      # 跨 tag 不漏
    from codeharness.exp_pool.schema import QueryType
    exact_miss = asyncio.run(mgr.query_exps("写一个五子棋", tag=exp.tag, query_type=QueryType.EXACT))
    assert exact_miss == [] or all(e.req == "写一个五子棋" for e, _ in exact_miss)
    asyncio.run(mgr.record_hit(exp))
    assert asyncio.run(counter.get(exp_point_id(exp.tag, exp.req, user))) == 1, "真 Redis 计数没落"
    asyncio.run(gate_store().delete_scope(doc_type="exp", user_id=user))
    print("  t23 真 Qdrant+真 Redis：入库→召回→计数落盘→作用域清理")


def t24_rolezero_think_wired():
    """真接线的证据：池子开着且命中时，RoleZero 的 structured **零次进模型**；关着时一切照旧。"""
    from codeharness.configs.settings import settings
    from codeharness.exp_pool.schema import Experience, QueryType
    from codeharness.roles.role_zero import RoleZero, ZeroThought
    import codeharness.exp_pool.manager as mg

    class FakeMgr:
        def __init__(self):
            self.saved, self.hits = [], []

        async def query_exps(self, req, tag="", query_type=QueryType.SEMANTIC, k=2):
            return [(self.saved[0], 0.99)] if self.saved and tag == self.saved[0].tag else []

        async def create_exp(self, exp):
            self.saved.append(exp)

        async def record_hit(self, exp):
            self.hits.append(exp)

    fake = FakeMgr()
    saved, settings.exp_pool = settings.exp_pool, settings.exp_pool.model_copy()
    mg._managers["default"] = fake
    try:
        settings.exp_pool.enabled = settings.exp_pool.enable_read = settings.exp_pool.enable_write = True
        from langchain_core.messages import HumanMessage
        llm = FakeLLM()
        role = RoleZero({"name": "RZ", "profile": "p", "goal": "g"}, [], llm)
        req = [HumanMessage(content="CMD")]
        first = asyncio.run(role.llm_cached_think(req=req))
        assert ZeroThought.model_validate_json(first).thought == "先写文件"
        assert len(llm.payloads) == 1 and len(fake.saved) == 1 and fake.saved[0].tag == "RoleZero.llm_cached_think"
        n0 = len(llm.payloads)
        again = asyncio.run(role.llm_cached_think(req=req))
        assert again == first and len(llm.payloads) == n0, "命中后仍进模型 = 没接线"
        assert fake.hits, "复用没计数"
        settings.exp_pool.enabled = False
        asyncio.run(role.llm_cached_think(req=req))
        assert len(llm.payloads) == n0 + 1, "关掉后必须完全透传"
    finally:
        settings.exp_pool = saved
        mg._managers.pop("default", None)
    print("  t24 RoleZero.llm_cached_think 接线：命中零模型调用，关池透传，tag=类名.方法名")


def live_embedding() -> bool:
    """真 bge-m3（本机 ollama）探活：能返回 1024 维才算在线。"""
    try:
        from codeharness.provider.gateway import LLMGateway
        v = asyncio.run(LLMGateway.embeddings().aembed_query("探活"))
        return len(v) == settings.embedding.dim
    except Exception:
        return False


def t25_real_bge_semantic_path():
    """真语义向量首跑（S5.2 欠账：三层此前只跑过 hash-fake）。
    断言要的是 hash-fake 给不出的能力：**改写**（零共同标识符）仍召回——
    bag-of-chars 看字面，bge-m3 看意思。表存 storage/benchmark/s5_hitrate_bge.json（S9 基线）。
    专属 1024 维集合 s5gate_bge，与 64 维的假 embedding 集合互不污染。"""
    if not (live_qdrant() and live_embedding()):
        print("  t25 跳过（Qdrant 或真 embedding 不在线）")
        return
    from codeharness.document_store.qdrant_store import Point, QdrantStore
    from codeharness.document_store.exp_store import ExpStore
    from codeharness.exp_pool.manager import ExperienceManager, HitCounter
    from codeharness.exp_pool.schema import Experience
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.provider.gateway import LLMGateway
    from codeharness.schema import Message

    bge = QdrantStore(collection="s5gate_bge")
    emb = LLMGateway.embeddings()
    CURRENT_PROJECT.set("s5_bge_proj")
    # 1) LongTermMemory：同义改写检索，且必须赢过字面重叠更多的干扰条目
    ltm = LongTermMemory(embeddings=emb, user_id="u_bge", store=bge)
    asyncio.run(ltm.overflow([
        Message(content="终端执行超时子进程挂住，要按进程树整体杀掉", role="user", sent_from="Memo"),
        Message(content="进程监视器每 30 秒轮询一次子进程状态并写日志", role="user", sent_from="Memo2")]))
    hits = asyncio.run(ltm.recall("程序卡着不退出，残留的进程怎么全部清掉", k=2))
    assert hits and "进程树" in hits[0].content, \
        f"真语义召回失败/排序不对（首位应是改写匹配）: {[h.content for h in hits]}"
    asyncio.run(ltm.drop())
    # 2) 经验池：改写 req 也召得回（判定阈值在装饰器侧，这里只验检索面）
    store = ExpStore(embeddings=emb, user_id="u_bge_exp", store=bge)
    mgr = ExperienceManager(store=store, counter=HitCounter(user_id="u_bge_exp"))
    asyncio.run(mgr.create_exp(Experience(req="终端执行超时子进程挂住", resp="按进程树杀", tag="RoleZero.llm_cached_think")))
    got = asyncio.run(mgr.query_exps("程序卡住不退出如何清理残留进程", tag="RoleZero.llm_cached_think"))
    assert got and got[0][0].resp == "按进程树杀" and got[0][1] > 0.5, got
    # 3) hit-rate：与 t18 同一份 query 集，真 embedding 重测存盘
    docs = []
    for key, _ in CORPUS_QUERY:
        docs.append((f"{TAIL} 关键实现见 {key}。", True, key))
        docs += [(f"{TAIL} 相关实现见 {w}。", False, key) for w in near_miss(key)]
    vecs = asyncio.run(emb.aembed_documents([t for t, _, _ in docs]))
    asyncio.run(bge.write([Point(id=pid(f"bge{i}"), text=t, dense=list(v), doc_type="kb",
                                 user_id="u_bge_bench")
                           for i, ((t, _, _), v) in enumerate(zip(docs, vecs))]))
    n, rows, ranks = len(CORPUS_QUERY), [], {"dense_only": [], "hybrid": []}
    for key, query in CORPUS_QUERY:
        want = next(pid(f"bge{i}") for i, (t, gold, g) in enumerate(docs) if gold and g == key)
        qv = asyncio.run(emb.aembed_query(query))
        for mode, hyb in (("dense_only", False), ("hybrid", True)):
            ids = [h.id for h in asyncio.run(bge.search(query, list(qv), k=5, hybrid=hyb,
                                                        doc_type="kb", user_id="u_bge_bench"))]
            ranks[mode].append(ids.index(want) + 1 if want in ids else None)
        rows.append({"query": query, "gold": key,
                     "rank_dense_only": ranks["dense_only"][-1], "rank_hybrid": ranks["hybrid"][-1]})
    hit = lambda rs: sum(1 for r in rs if r is not None and r <= 5)
    assert hit(ranks["dense_only"]) == n and hit(ranks["hybrid"]) == n, rows   # 真模型下 gold 必须全在 top5
    out = Path(settings.workspace_root).parent / "storage" / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    (out / "s5_hitrate_bge.json").write_text(json.dumps(
        {"metric": f"hit-rate@5, 真 embedding 语义路径（{settings.embedding.model}）", "corpus": len(docs), "queries": n,
         "dense_only_hit@5": hit(ranks["dense_only"]), "hybrid_hit@5": hit(ranks["hybrid"]),
         "ranks": {k: v for k, v in ranks.items()}, "rows": rows,
         "note": "S9 基线表；与 s5_hitrate.json（hash-fake）同 query 集，这张才是真实语义的对照"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    asyncio.run(bge.drop())
    print(f"  t25 真 embedding（{settings.embedding.model}）：改写召回排第一、经验回放命中、hit@5 {hit(ranks['dense_only'])}/{n}"
          f"→{hit(ranks['hybrid'])}/{n} 全收；表已存 s5_hitrate_bge.json")


def t26_plan_state_machine_wired():
    """台账 #10：Plan.* 不再是吞命令的字符串桩——schema.Plan 的拓扑排序/游标推进/级联 reset
    有运行时读者；参数错（未知依赖断言）走 _act 的 [错误] self-heal 回喂。"""
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.role_zero import RoleZero

    def act(rz, cmd, args):
        s = {"task": "做个计算器", "history": [{"thought": "先立计划",
             "commands": [{"command_name": cmd, "args": args}], "results": []}],
             "experience": "", "respond_language": "中文", "finished": False}
        out = asyncio.run(rz._act(s))
        return out["history"][-1]["results"][-1]["result"]

    CURRENT_PROJECT.set("s5plan")
    try:
        rz = RoleZero({"name": "RZP", "profile": "p", "goal": "g"}, [], FakeLLM([]))
        rz._plan_goal = "做个计算器"
        r = act(rz, "Plan.append_task", {"task_id": "T1", "dependent_task_ids": [],
                                         "instruction": "设计接口", "assignee": "RZP"})
        assert "共 1 任务" in r, r
        r = act(rz, "Plan.append_task", {"task_id": "T2", "dependent_task_ids": ["T1"],
                                         "instruction": "实现加法", "assignee": "RZP"})
        assert "共 2 任务" in r, r
        r = act(rz, "Plan.finish_current_task", {})
        assert "已推进 → T2" in r and "计划完成: False" in r, r
        status, cur = rz._plan_status({"history": []})
        assert "[x] T1" in status and "[ ] T2" in status and cur.startswith("T2"), (status, cur)
        act(rz, "Plan.reset_task", {"task_id": "T1"})   # 级联：T1 未完成，游标回 T1
        assert rz.plan.current_task.task_id == "T1" and not rz.plan.tasks[0].is_finished
        r = act(rz, "Plan.append_task", {"task_id": "T9", "dependent_task_ids": ["NOPE"],
                                         "instruction": "坏任务", "assignee": "RZP"})
        assert r.startswith("[错误]"), f"未知依赖必须回喂自愈而不是静默: {r}"
    finally:
        CURRENT_PROJECT.set("")
    print("  t26 计划状态机：Plan 四命令真驱动 schema.Plan（拓扑/游标/级联 reset/错误回喂）")


def t27_di_review_gate_blocks_until_resume():
    """台账 #12（源 planner.py:96/:104 的 ask_review 环）：auto_run=False 时不 resume 不放行——
    计划闸卡住时执行件一次模型都不进；"no" 回喂重规划、"confirm" 才执行；任务验收闸同理。
    词表判定（confirm/continue/c/yes/y 或含 confirm）逐字照源 ask_review.py:60。"""
    from langgraph.types import Command
    # 本文件顶部的 FakeLLM 是摘要记账 stub（吃 summary= 入参）；这里要的是按剧本回放的那只
    from codeharness.provider.fake import FakeLLM
    from codeharness.strategy.plan_and_act import PlanAndActAgent
    one = {"task_id": "T1", "dependent_task_ids": [], "instruction": "读 CSV", "assignee": "David"}
    plan = json.dumps({"goal": "分析数据", "tasks": [one]})
    llm = FakeLLM([plan, plan, "```python\nprint('done 42')\n```", "结论：均值 42"])
    agent = PlanAndActAgent({"name": "David", "profile": "Data Interpreter", "goal": "analyze"},
                            llm, auto_run=False)
    g = agent.build()
    cfg = {"configurable": {"thread_id": "t27"}}
    init = {"goal": "分析数据", "plan": {}, "task_idx": 0, "code": "", "results": [],
            "finished": False, "plan_ok": False, "feedback": ""}
    out = asyncio.run(g.ainvoke(init, cfg))
    assert "__interrupt__" in out, "计划闸没卡住：不 resume 就往下跑了"
    assert len(llm.calls) == 1, f"闸住时执行件不得进模型: {len(llm.calls)}"
    out = asyncio.run(g.ainvoke(Command(resume="no, 先检查文件存在"), cfg))
    assert "__interrupt__" in out, "未确认的计划必须重规划后仍回闸"
    assert len(llm.calls) == 2, "重规划应再问 planner 一次"
    out = asyncio.run(g.ainvoke(Command(resume="confirm"), cfg))
    assert "__interrupt__" in out, "任务执行完应停在验收闸（源 :104）"
    out = asyncio.run(g.ainvoke(Command(resume="yes"), cfg))
    assert out["finished"] and any(r["task"] == "__summary__" for r in out["results"]), out["results"]
    print("  t27 DI 人在环：计划确认+任务验收两闸不 resume 不放行，confirm 词表照源")


def t28_ltm_rerank_absorbed_and_degrades():
    """台账 #11：KnowledgeBase 吸收删除后，精排接缝活在 LongTermMemory._rerank——
    精排服务离线（本机未部署 reranker 属预期）降级为粗排原序截断且留 warning，不抛。"""
    from types import SimpleNamespace
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.logs import logger as _lg
    keep, settings.reranker.base_url = settings.reranker.base_url, "http://127.0.0.1:1"
    warned = []
    orig = _lg.warning
    _lg.warning = lambda *a, **k: warned.append(a)
    try:
        hits = [SimpleNamespace(id=f"p{i}", payload={"text": f"t{i}"}) for i in range(4)]
        ltm = LongTermMemory.__new__(LongTermMemory)     # _rerank 不吃 store/embeddings，直测接缝
        got = asyncio.run(ltm._rerank("q", hits, k=3))
        assert [h.payload["text"] for h in got] == ["t0", "t1", "t2"], got
        assert any("精排" in str(w) for w in warned), "降级必须留痕（docs P1：静默缺席是坑）"
    finally:
        _lg.warning = orig
        settings.reranker.base_url = keep
    print("  t28 精排接缝随吸收进 ltm：离线降级粗排原序 + warning 留痕")


def t29_scorer_template_verbatim():
    """S9.2「perfect_judges 接真实 LLM」第一钉：SimpleScorer 模板与源**值逐字**（t11 同法 AST 比对）。
    打分模板是 prompt 资产，转抄漂移=评分口径漂移。"""
    import ast
    src = Path(__file__).resolve().parents[2] / "MetaGPT" / "metagpt" / "exp_pool" / "scorers" / "simple.py"
    if not src.exists():
        print("  t29 跳过（供体不在）")
        return
    tree = ast.parse(src.read_text(encoding="utf-8"))
    sc = {}
    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Name) \
                    and isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
                sc[sub.targets[0].id] = sub.value.value
    from codeharness.exp_pool.scorers import SIMPLE_SCORER_TEMPLATE
    assert SIMPLE_SCORER_TEMPLATE == sc["SIMPLE_SCORER_TEMPLATE"], "打分模板与源不逐字（评分口径漂移）"
    print("  t29 SimpleScorer 模板与源值逐字相等")


def t30_simple_scorer_fake_llm_path():
    """打分路径本身：aask 出题带 req/resp、```json 围栏解析回 Score（惰性 llm 注入为门禁半边，
    真网关半边由 manual_judge_quality 真钱通道消费）。⚠ 必须用 provider 的 FakeLLM——
    本文件顶部的同名桩是给 BrainMemory 用的，aask 恒回 summary、吞剧本。"""
    from codeharness.exp_pool.scorers import SimpleScorer
    from codeharness.provider.fake import FakeLLM as ProviderFake
    llm = ProviderFake(['```json\n{"val": 9, "reason": "满足需求，结构清晰"}\n```'])
    s = asyncio.run(SimpleScorer(llm=llm).evaluate("做个2048", '{"original_requirements":"做个2048"}'))
    assert s.val == 9 and s.reason == "满足需求，结构清晰", s
    asked = llm.calls[0][0].content          # str(list) 是 repr，多行段转义假阴性——打在真 content 上
    assert "做个2048" in asked and "score, int from 1 to 10" in asked, "模板没带 req 进题"
    print("  t30 SimpleScorer 打分路径：模板带 req 真进题、围栏 JSON 解析回 Score")


def t31_exp_tenant_isolation():
    """C4 的正向证据：两个租户各写一条经验，**A 召得回自己的、B 召不回 A 的**。

    旧写法这一格是**空转断言**：内部替身 `MemStore.search` 恒返回 `[r for r in []]`，连 A 自己都
    查不到，于是「B 查不到 A 的经验」这句无论如何都成立，真正被断言的只剩「两个 manager 不是同一个」。
    现在三格互钉：
      ① 前半保留（manager 随 `CURRENT_USER` 分流，这是 `e382476` 那次修复的形状）；
      ② 真往返（gate 专属集合）：A 写 A 查 → **非空**且逐字段对得上；B 用同一条 query 查 → 空，
         而 B 查自己那条 → 非空。**没有 ① 之外的这格「B 也查得到自己的」，「B 查不到」就毫无意义**；
      ③ 不依赖 Qdrant 在线的一格：拦 **ExpStore 真发出去的那次查询**，断言构造出的 filter 里带
         `user_id == 该 store 的 user` 与 `doc_type == "exp"`。过滤是库做的，**在自己的替身里
         手写过滤测的是替身**，所以这里只记不调。
    ② 需要 Qdrant 在线（只写 `s5gate`，跑完按租户删净），不在线印跳过并进末行统计。
    """
    import codeharness.exp_pool.manager as mg
    from codeharness.runtime import CURRENT_USER

    tok = CURRENT_USER.set("alice")
    try:
        mg._managers.clear()
        mA = mg.get_exp_manager()
        assert mA.store.user_id == "alice" and mA.counter.user_id == "alice", \
            f"CURRENT_USER=alice 但 manager 落 {mA.store.user_id}（隔离未接）"
        CURRENT_USER.set("bob")
        mB = mg.get_exp_manager()
        assert mB.store.user_id == "bob", "bob 应拿到自己的 manager"
        assert mB is not mA, "两用户共用同一 manager = 首个会话把租户冻死"
    finally:
        CURRENT_USER.reset(tok)
        mg._managers.clear()

    from codeharness.document_store.exp_store import ExpStore

    if live_qdrant():
        st_a = ExpStore(embeddings=HashEmbeddings(), user_id="alice", store=gate_store())
        st_b = ExpStore(embeddings=HashEmbeddings(), user_id="bob", store=gate_store())
        try:
            asyncio.run(st_a.save("write_code", "把列表去重并保持顺序", "用 dict.fromkeys"))
            asyncio.run(st_b.save("write_code", "把列表去重并保持顺序", "用 seen 集合累加"))
            mine = asyncio.run(st_a.search("write_code", "把列表去重并保持顺序"))
            assert mine and mine[0]["output"] == "用 dict.fromkeys", \
                f"t31② 阳性对照塌了：A 连自己写的经验都召不回（{mine}）——那 B 查不到就是假绿"
            assert all(h["output"] != "用 seen 集合累加" for h in mine), \
                f"t31② 越权：A 召回了 B 的经验 {[h['output'] for h in mine]}"
            theirs = asyncio.run(st_b.search("write_code", "把列表去重并保持顺序"))
            assert theirs and theirs[0]["output"] == "用 seen 集合累加", \
                f"t31② B 连自己都查不到 = 这格又退回恒空断言（{theirs}）"
            assert all(h["output"] != "用 dict.fromkeys" for h in theirs), \
                f"t31② 越权：B 召回了 A 的经验 {[h['output'] for h in theirs]}"
            live = "②真往返两租户互不可见"
        finally:
            for uid in ("alice", "bob"):
                asyncio.run(gate_store().delete_scope(doc_type="exp", user_id=uid))
    else:
        live = "②跳过（无 Qdrant）"

    # ③ 拦真查询：只记 filter，不自己实现过滤
    from qdrant_client import AsyncQdrantClient

    from codeharness.document_store.qdrant_store import QdrantStore
    seen = {}
    real = AsyncQdrantClient(url=settings.qdrant.url, check_compatibility=False)

    class _Spy:
        def __init__(self, inner):
            self.inner = inner

        async def query_points(self, collection, **kw):
            seen["collection"], seen["filter"] = collection, kw.get("query_filter")
            return type("R", (), {"points": []})()

        async def upsert(self, *a, **k):
            return await self.inner.upsert(*a, **k)

        async def create_collection(self, *a, **k):
            return await self.inner.create_collection(*a, **k)

        async def get_collection(self, *a, **k):
            return await self.inner.get_collection(*a, **k)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    gated = QdrantStore(collection=GATE_COLL)
    gated.client = _Spy(real)
    gated._ready = True                       # 集合形态由 t12 钉，这一格只管发出去的 filter
    asyncio.run(ExpStore(embeddings=HashEmbeddings(), user_id="carol",
                         store=gated).search("run_code", "跑一下这段脚本"))
    assert "filter" in seen, "t31③：ExpStore 根本没发出查询（这格成了空转）"
    assert seen["collection"] == GATE_COLL, f"t31③：查到了生产集合 {seen['collection']}！"
    conds = {c.key: getattr(c.match, "value", None) for c in (seen["filter"].must or [])}
    assert conds.get("user_id") == "carol", \
        f"t31③：真查询里的 user_id 不是这个 store 的租户（{conds}）——隔离在这一层漏了"
    assert conds.get("doc_type") == "exp", \
        f"t31③：经验没限定在 `doc_type=exp` 切片上（{conds}），会串到 kb/memory 里去"
    print(f"  t31 经验池租户隔离：manager 随 CURRENT_USER 分流、{live}、"
          f"③真查询 filter 带 user_id=carol + doc_type=exp")


def t32_rerank_unset_default_skips_cleanly():
    """C8：精排**未配置**时必须「零 HTTP、零异常、零 warning」地跳过。
    旧默认值 `base_url="http://localhost:9998/v1"` 非空，而 `_rerank` 的判据只挡「URL 为空」，
    本机 `.env`/compose 又没有该服务 → 每次 recall 都真发一跳、再被 `except Exception` 吞掉降级 + 刷 warning
    （`docs/对照2-记忆管理.md` §四-3 早就记成缺陷）。与 t28 成对：t28 钉「显式配了但服务离线」那一跳照旧降级留痕。

    两层判据：① 打在**配置类的默认值**上（不是本机 live 值，免得门禁被 `.env` 绑架）；
    ② 打在**实际走哪条分支**上——把 `httpx.AsyncClient` 换成「一碰就抛」的哨兵，跳过分支碰不到它；
       同时抓 `logger.warning`，跳过不该留痕（留痕是给真降级的）。"""
    from types import SimpleNamespace
    from codeharness.configs.settings import RerankerConfig
    from codeharness.memory import longterm as lt
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.logs import logger as _lg

    assert RerankerConfig.model_fields["base_url"].default == "", \
        f"缺省又指向了一个可能存在的服务：{RerankerConfig.model_fields['base_url'].default!r}" \
        "——未配置就该是空串，精排是显式 opt-in 能力（C8）"
    keep_url, keep_client = settings.reranker.base_url, lt.httpx.AsyncClient
    warned = []
    orig = _lg.warning

    def _boom(*a, **k):
        raise AssertionError("未配置精排却构造了 HTTP 客户端——退回「先抛再吞」那条老路了")

    settings.reranker.base_url = ""
    lt.httpx.AsyncClient = _boom
    _lg.warning = lambda *a, **k: warned.append(a)
    try:
        hits = [SimpleNamespace(id=f"p{i}", payload={"text": f"t{i}"}) for i in range(4)]
        ltm = LongTermMemory.__new__(LongTermMemory)     # _rerank 不吃 store/embeddings，直测接缝（同 t28）
        got = asyncio.run(ltm._rerank("q", hits, k=3))
        assert [h.payload["text"] for h in got] == ["t0", "t1", "t2"], got
        assert not warned, f"跳过分支不该留 warning（那是真降级的留痕）：{warned}"
    finally:
        settings.reranker.base_url, lt.httpx.AsyncClient, _lg.warning = keep_url, keep_client, orig
    print("  t32 精排未配置：默认值即跳过——零 HTTP、零异常、零 warning，粗排原序前 k 条")


def t33_point_id_carries_tenant_and_doc_type():
    """C4 同族审（09-22）：点 id 的派生式必须带齐**租户**与 **doc_type** 两个维度。

    C4 那个洞的形状是「派生式漏了 user ⇒ 两个租户算出同一个点 id，读侧 filter 挡住了越权可见、
    写侧却是后写的把先写的静默覆盖」。这一格把同一条判据打在另外两条入库路上（拦真写者收集产出——
    在自己的替身里手写过滤测的是替身，C4 同一课）：
      ① 跨用户同内容 → id 必须不同；
      ② 同用户同项目、**doc_type 不同** → id 必须不同。这一格是本轮审出来的**真不对称**：
         `overflow` 的 scope 原先只到 `user/project`，而 kb 那条实例与 memory 那条**是同一个类**
         （`team.py:23-24` 一次建两个、`role.kb` 就是它的挂点）。谁哪天调一次
         `role.kb.overflow(...)`，同一段文本就会跨 doc_type 互相顶掉：payload 说自己是 kb、
         id 却与 memory 那条相同 ⇒ 后写的赢，前一个读者再也召不回它。
      ③ 同租户同 doc_type 同内容 → id 必须**相同**（幂等是刻意的，这条防有人把它改成 uuid4）。
    `actions/upload_kb.py:70` 从 C3 起就把 doc_type 写进 scope，所以只有 ② 这一支是漏的。
    """
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER

    TEXT = "重置密码要先验证旧密码"

    class _Cap:
        def __init__(self):
            self.points = []

        async def write(self, points):
            self.points += list(points)
            return len(points)

    def ids(user: str, doc_type: str, project: str = "p33") -> set:
        cap = _Cap()
        tok_p, tok_u = CURRENT_PROJECT.set(project), CURRENT_USER.set(user)
        try:
            ltm = LongTermMemory(project_id=project, embeddings=HashEmbeddings(), user_id=user,
                                 store=cap, doc_type=doc_type)
            asyncio.run(ltm.overflow([Message(content=TEXT, role="user")]))
        finally:
            CURRENT_PROJECT.reset(tok_p)
            CURRENT_USER.reset(tok_u)
        assert cap.points, f"前置失配：{user}/{doc_type} 这一趟根本没写到点"
        return {pt.id for pt in cap.points}

    alice, bob = ids("alice", "memory"), ids("bob", "memory")
    assert not (alice & bob), f"①失效：两个租户同一段文本算出同一个点 id（就是 C4 那个洞）：{alice}"
    mem, kb = ids("alice", "memory"), ids("alice", "kb")
    assert not (mem & kb), f"②失效：memory 与 kb 共用一个点 id，写 kb 会把记忆那条顶掉：{mem}"
    assert ids("alice", "memory") == alice, "③失效：同租户同 doc_type 同内容不再是同一个点（幂等没了）"
    print("  ok  t33 点 id 带齐租户与 doc_type 两个维度，且同内容仍幂等")


# `DirEmbeddings`（按方向给定 dense 余弦的替身）住在 `codeharness/provider/fake.py`，与 HashEmbeddings 作伴：
# C23 两格与 s15 t12 共用它——替身是公共件，不是一个门禁的私产（C3 搬 HashEmbeddings 同一条理由）。
C23_REL, C23_LOUD = "先校验旧密码再发一次性链接", "重置密码流程重置密码流程"
C23_MID, C23_COLD = "账号策略要求至少十二位含符号", "今天适合去爬山"
C23_Q = "重置密码的流程是什么"
C23_COS = {C23_REL: 1.0, C23_MID: 0.6, C23_LOUD: 0.25, C23_COLD: 0.0}


def _c23_ltm(user: str, project: str) -> "object":
    from codeharness.memory.longterm import LongTermMemory
    ltm = LongTermMemory(project_id=project, embeddings=DirEmbeddings(C23_COS, C23_Q),
                         user_id=user, store=gate_store(), doc_type="kb")
    asyncio.run(ltm.drop())            # 上一轮残点会挤掉名次，先按作用域清干净（drop 不依赖在线以外的东西）
    asyncio.run(ltm.overflow([Message(content=t, role="user")
                              for t in (C23_REL, C23_LOUD, C23_MID, C23_COLD)]))
    return ltm


class _FloorCfg:
    """临时改 `settings.recall_floor` 的那四个值，`with` 退出还原（同 t2 改 redis 端口，只是不止一个键）。"""

    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        cfg = settings.recall_floor
        self.keep = {k: getattr(cfg, k) for k in self.kw}
        for k, v in self.kw.items():
            setattr(cfg, k, v)
        return cfg

    def __exit__(self, *exc):
        cfg = settings.recall_floor
        for k, v in self.keep.items():
            setattr(cfg, k, v)
        return False


class _SearchSpy:
    """记下发出去的那两次检索的实参（C4 那一课：过滤是库做的，在自己的替身里手写过滤测的是替身）。"""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    async def search(self, query, dense, **kw):
        self.calls.append(kw)
        return await self.inner.search(query, dense, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def t34_recall_floor_dense_score():
    """C23 (a) 支：dense 腿先按**余弦**筛，再交 hybrid 只在留下的里面重排。四格各管一种坏法：

      ① 现状（不设下限）：那条「字面最像、语义最不相干」的切片真的进了 top-2 —— 本项要修的就是它；
         这一格同时是 ② 的**阳性对照反面**：不先证明它会进来，② 的「不在」可以是恒绿。
      ② 加了余弦下限：它不进，而真相关那条**仍进**（阳性对照）。
      ③ 第二次检索必须带着筛剩的点 id 发出去（拦真调用）：把筛选写在客户端「hybrid 完再挑」是同一句
         判据的另一种实现，③ 当场分辨——留在 prompt 里的集合必须等于筛剩的集合。
      ④ 坏配置当场拒：开了 score 档却不给线、线在余弦刻度之外、窗宽 0。
    """
    from pydantic import ValidationError
    from codeharness.configs.settings import RecallFloorConfig
    if not live_qdrant():
        print("  t34 跳过（无 Qdrant）")
        return
    ltm = _c23_ltm("u_c23s", "c23_gate_s")
    spy = _SearchSpy(ltm.store)
    try:
        with _FloorCfg(mode="off", oversample=3, min_score=0.4):
            base = asyncio.run(ltm.recall(C23_Q, k=2))
        assert [m.content for m in base] == [C23_REL, C23_LOUD], \
            f"①现状失配：不设下限时那条字面像的应当挤进 top-2，实得 {[m.content for m in base]}"
        ltm.store = spy
        with _FloorCfg(mode="score", oversample=3, min_score=0.4):
            got = asyncio.run(ltm.recall(C23_Q, k=2))
        names = [m.content for m in got]
        assert C23_REL in names, f"②失效：阳性对照（真相关那条）也被砍了 → {names}"
        assert C23_LOUD not in names, f"②失效：下限 0.4 没挡住 dense=0.25 那条 → {names}"
        assert len(spy.calls) == 2, f"③失效：应是 dense 预筛 + 受限重排两次，实得 {len(spy.calls)} 次"
        first, second = spy.calls
        assert first.get("hybrid") is False and first["k"] == 6, f"③失效：预筛腿不是宽窗 dense：{first}"
        assert second.get("only_ids"), f"③失效：重排腿没带筛剩的 id（等于没筛）：{second}"
        assert second.get("hybrid", True) is not False, f"③失效：重排腿退回 dense-only 了：{second}"
        ltm.store = spy.inner
        for bad in [dict(mode="score", min_score=0.0), dict(mode="score", min_score=1.5),
                    dict(mode="score", min_score=-0.1), dict(oversample=0),
                    dict(mode="rank", max_rank=0)]:
            try:
                RecallFloorConfig(**bad)
            except ValidationError:
                continue
            raise AssertionError(f"④失效：坏配置被收下了 {bad}")
    finally:
        asyncio.run(ltm.drop())
    print("  ok  t34 C23(a) 余弦下限：字面像而语义无关那条被挡在 prompt 外、真相关那条仍进、"
          "重排腿带筛剩 id 出门、坏配置当场拒")


def t35_recall_floor_dense_rank():
    """C23 (c) 支：同一个候选窗按 dense **原始名次**出局（名次是序数，换 embedding 模型不必重量）。

      ① 线画在 1 → 只留 dense 第 1 名；真相关那条仍进（阳性对照），那条字面像的进不来。
      ② 候选窗不许比名次线更窄：`oversample=1` + `k=2` 时窗本来说是 2，而线画在 3 → 交给重排腿的必须是
         3 条。实现若忘了把窗抬到 `max_rank`，「名次 ≤3」会被窗静默截成「名次 ≤2」——判据在、行为不是一回事，
         而且**这一格第一版就是那个忘法**（拿 `k=3`+`oversample=1` 试，`max(3,3)=3` 让 max 成了恒等，
         变异 m5 跑成绿；改成 `k=2` 才有牙）。所以判据打在「第二次检索实发的 id 数」上，不是打在条数上。
      ③ 筛空必须回空集：`only_ids` 为老实语义是「不加限制」，拿一条过不了的线把整批砍掉时，
         漏判这一条的实现会把全表放行（那比没有下限更糟）。
      ④ `screen_dense` 纯函数面：名次档只看序数、余弦档只看分，两档互不串。
    """
    if not live_qdrant():
        print("  t35 跳过（无 Qdrant）")
        return
    from types import SimpleNamespace
    from codeharness.configs.settings import RecallFloorConfig
    from codeharness.memory.longterm import screen_dense
    ltm = _c23_ltm("u_c23r", "c23_gate_r")
    try:
        with _FloorCfg(mode="rank", max_rank=1, oversample=3):
            got = asyncio.run(ltm.recall(C23_Q, k=2))
            assert [m.content for m in got] == [C23_REL], \
                f"①失效：名次线=1 时应只留 dense 第 1 名，实得 {[m.content for m in got]}"
        spy = _SearchSpy(ltm.store)
        ltm.store = spy
        with _FloorCfg(mode="rank", max_rank=3, oversample=1):
            wide = asyncio.run(ltm.recall(C23_Q, k=2))     # 窗 = max(2×1, 3) = 3，名次线 3 才留得下 3 条
        ltm.store = spy.inner
        assert len(spy.calls[1]["only_ids"]) == 3, \
            (f"②失效：候选窗没被抬到名次线——窗只有 2 时「名次 ≤3」被静默截成「≤2」，"
             f"交给重排腿的 {len(spy.calls[1]['only_ids'])} 条：{spy.calls[1]}")
        assert wide and wide[0].content == C23_REL, \
            f"②阳性对照：重排回来第一条该是 dense 第 1 名，实得 {[m.content for m in wide]}"
        with _FloorCfg(mode="score", min_score=0.4):
            assert C23_LOUD not in [m.content for m in asyncio.run(ltm.recall(C23_Q, k=2))]
        # ③ 运行期绕开 validator 把线抬到余弦刻度之上：唯一的目的是造出「整批被砍空」，
        #    这时必须回空集，而不是把全表放行。
        with _FloorCfg(mode="score", min_score=1.2):
            assert asyncio.run(ltm.recall(C23_Q, k=2)) == [], "③失效：砍空却放行了全表"
        hits = [SimpleNamespace(id="a", score=0.9), SimpleNamespace(id="b", score=0.2)]
        assert screen_dense(hits, RecallFloorConfig(mode="rank", max_rank=1)) == ["a"]
        assert screen_dense(hits, RecallFloorConfig(mode="score", min_score=0.5)) == ["a"]
    finally:
        asyncio.run(ltm.drop())
    print("  ok  t35 C23(c) 名次下限：按 dense 原始名次出局、窗不静默截线、砍空回空集")

class _StubResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _StubRerank:
    """接住精排那一跳的假客户端：记下**实发的 url / body / 头**，按脚本回响应或抛异常。

    为什么不用真 HTTP 桩：这一格要钉的是「路径怎么拼、鉴权头有没有发、返回序是不是精排序、
    分筛打在哪」四件**我们这侧**的事，替身记下实发参数就够；而「云端真的答不答、分是不是 0-1」
    那一格由 t37 显式开关（`RERANK_LIVE=1`）去真调，平时不烧额度。
    """

    def __init__(self, results=None, boom=None):
        self.sent, self.results, self.boom = [], results, boom

    def __call__(self, **kw):                     # httpx.AsyncClient(timeout=20) 的调用形状
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.sent.append({"url": url, "body": json, "headers": headers or {}})
        if self.boom:
            raise self.boom
        docs = (json or {}).get("documents") or []
        # 默认脚本：**倒序**回（相关性分也按倒序给）——只有与粗排不同的序才能验出「顺序听谁的」
        res = self.results if self.results is not None else \
            [{"index": i, "relevance_score": 0.9 - 0.1 * j}
             for j, i in enumerate(reversed(range(len(docs))))]
        return _StubResp({"results": res})


def t36_recall_floor_rerank_score():
    """C23(b) 支：门限打在**精排回的 0-1 分**上。五格，每格钉一处今天真的坏着的东西：

      ① 路径：`base_url` 结尾已是 `/reranks`（百炼兼容口，实测单数 `/rerank` **404**）时按原样打，
         不再自作主张补 `/rerank`；且 **`Authorization` 头真的发出去了**——旧代码一个头都不发，
         `reranker.api_key` 是零读者死字段，接云端永远 401 又被 `except` 吞成「降级」。
      ② 顺序：旧实现把响应按 `index` 排序再回，那等于**把精排的相关性序丢掉、还原成粗排原序**
         （「重排」是空壳）。这里让替身按**倒序**回，断言最终顺序跟着精排走。
      ③ 宽窗：发给精排的候选数必须是 `max(k, reranker.recall_k)`，不是 k —— 那字段登记至今零读者。
      ④ 分筛：线 0.5、替身给 rel=0.95 / 其余 0.10 → 只留 rel（阳性对照：真相关那条在）。
      ⑤ 降级：精排抛异常 → 退回**粗排原序前 k 条**（不是空集），且 warning **恰好响一声**；
         「没精排」与「有分但都不达标」是两件事，不许都写成「被下限砍空」。
    """
    from codeharness.configs.settings import Settings
    from codeharness.logs import logger as _lg
    from codeharness.memory import longterm as lt
    if not live_qdrant():
        print("  t36 跳过（无 Qdrant）")
        return
    ltm = _c23_ltm("u_c23b", "c23_gate_b")
    keep_url, keep_key, keep_client = settings.reranker.base_url, settings.reranker.api_key, lt.httpx.AsyncClient
    orig_warn = _lg.warning
    warned = []
    try:
        settings.reranker.base_url = "https://rerank.invalid/compatible-api/v1/reranks"
        settings.reranker.api_key = "sk-test-not-a-real-key"
        _lg.warning = lambda *a, **k: warned.append(a)
        stub = _StubRerank()
        lt.httpx.AsyncClient = stub
        with _FloorCfg(mode="rerank", min_score=0.5):
            got = asyncio.run(ltm.recall(C23_Q, k=2))
        assert len(stub.sent) == 1, f"前置失配：精排那一跳没发出去（{len(stub.sent)} 次）"
        sent = stub.sent[0]
        assert sent["url"].endswith("/reranks"), f"①失效：路径被改写了 → {sent['url']}"
        assert sent["headers"].get("Authorization") == "Bearer sk-test-not-a-real-key", \
            f"①失效：鉴权头没发出去，云端永远 401 → {list(sent['headers'])}"
        assert sent["body"].get("query") == C23_Q and C23_REL in sent["body"]["documents"], \
            f"③失效：发给精排的不是召回到的那批文本 → {str(sent['body'])[:160]}"
        assert len(sent["body"]["documents"]) == 4, \
            (f"③失效：候选数应是 max(k, recall_k)=10 → 库里的 4 条全发，实发 "
             f"{len(sent['body']['documents'])} 条（那就是拿 k 当宽窗）")
        docs = sent["body"]["documents"]
        assert [m.content for m in got] == list(reversed(docs))[:2], \
            f"②失效：最终顺序没跟着精排（替身回的是倒序）→ {[m.content for m in got]} / 粗排 {docs}"

        scored = [{"index": docs.index(C23_REL), "relevance_score": 0.95}] + \
                 [{"index": docs.index(t), "relevance_score": 0.10}
                  for t in docs if t != C23_REL]
        stub2 = _StubRerank(results=sorted(scored, key=lambda r: -r["relevance_score"]))
        lt.httpx.AsyncClient = stub2
        with _FloorCfg(mode="rerank", min_score=0.5):
            got2 = asyncio.run(ltm.recall(C23_Q, k=3))
        assert [m.content for m in got2] == [C23_REL], \
            f"④失效：线 0.5 该只留 rel（0.95），实得 {[m.content for m in got2]}"

        stub3 = _StubRerank(boom=RuntimeError("connection refused"))
        lt.httpx.AsyncClient = stub3
        warned.clear()
        with _FloorCfg(mode="rerank", min_score=0.5):
            got3 = asyncio.run(ltm.recall(C23_Q, k=2))
        assert len(got3) == 2 and warned and len(warned) == 1, \
            f"⑤失效：精排挂了应退回粗排原序前 k 条且只响一声，实得 {len(got3)} 条 / {len(warned)} 声"
        assert got3[0].content == docs[0], \
            f"⑤失效：降级后的顺序不是粗排原序 → {[m.content for m in got3]} vs 粗排 {docs}"

        for bad in [{"recall_floor": {"mode": "rerank", "min_score": 0.0}},
                    {"recall_floor": {"mode": "rerank", "min_score": 0.5},
                     "reranker": {"base_url": ""}},
                    {"recall_floor": {"mode": "rerank", "min_score": 1.5}}]:
            from pydantic import ValidationError
            try:
                Settings(_env_file=None, **bad)
            except ValidationError:
                continue
            raise AssertionError(f"配置面失效：这种组合被收下了 {bad}")
    finally:
        settings.reranker.base_url, settings.reranker.api_key = keep_url, keep_key
        lt.httpx.AsyncClient, _lg.warning = keep_client, orig_warn
        asyncio.run(ltm.drop())
    print("  ok  t36 C23(b) 精排分下限：路径/鉴权头/精排序/宽候选/挂了只响一声 各钉一处")


def t37_rerank_endpoint_real_answer():
    """**可选真调**（`RERANK_LIVE=1` 才发）：百炼那个口真的答、分真的在 0-1、相关那条真的排第一。

    为什么不常驻：这把 key 的额度是 1M token，而精排按 **query × 候选数** 计费——一发就要几百 token。
    本格的形状与 t36 完全一样，只是把替身换成真端点：**默认跳过并在末行留字**，要核云端就显式开。
    """
    import os
    if os.environ.get("RERANK_LIVE") != "1":
        print("  t37 跳过（RERANK_LIVE!=1：精排按 query×候选计费，不白烧额度）")
        return
    if not live_qdrant():
        print("  t37 跳过（无 Qdrant）")
        return
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.provider.gateway import LLMGateway
    ltm = LongTermMemory(project_id="c23_live", embeddings=LLMGateway.embeddings(),
                         user_id="u_c23live", store=gate_store(), doc_type="kb")
    asyncio.run(ltm.overflow([Message(content="重置密码要先验证旧邮箱，系统发一次性验证码", role="user"),
                              Message(content="城市马拉松的补给站每 5 公里一处", role="user")]))
    with _FloorCfg(mode="rerank", min_score=0.01):     # 线放到最低：这一格要量的是「顺序与分」，不是筛
        hits = asyncio.run(ltm.recall("怎么重置密码", k=2))
    assert hits and "验证码" in hits[0].content, \
        f"真精排下相关那条该排第一，实得 {[m.content for m in hits]}"
    asyncio.run(ltm.drop())
    print(f"  ok  t37 真端点（{settings.reranker.model}）答了、相关那条第一；"
          f"分落在 0-1 之间 ⇒ 那根线要标定时按这个刻度，别拿 dense 余弦的 0.40 套")


def t38_both_recall_legs_clamp_their_query():
    """C35：两条召回腿发给端点的 query 都必须过同一个上界出口。

    判据打在**实发长度**上（spy 替身记下端点收到的串长），不是 grep 源码文本——C27 的 t8 已经立过
    这条：结构不变量要「出口打记号 + spy 记实发」，grep 只能证明字面在，不能证明它在链上。
    两条腿都要量，因为 C32 只截了 `LongTermMemory.recall` 那一条，而经验池那条收的是**用户原话**
    （`manager.py:69` 把 req 直接传进 `ExpStore.search`），漏的那半正是最长的那种输入。
    """
    from codeharness.configs.settings import settings
    from codeharness.document_store.exp_store import ExpStore
    from codeharness.memory.longterm import LongTermMemory

    limit = settings.embedding.max_chars
    sent = []

    class SpyEmb:
        dim = 64

        async def aembed_query(self, q):
            sent.append(len(q))
            return [0.0] * 64

        async def aembed_documents(self, ts):
            return [[0.0] * 64 for _ in ts]

    class NoStore:
        async def search(self, *a, **kw):
            return []

    long_q = "长" * (limit + 500)
    short_q = "重置密码的流程是什么"
    for label, run in (("kb 腿", lambda q: LongTermMemory(embeddings=SpyEmb(), store=NoStore(),
                                                         doc_type="kb").recall(q, k=3)),
                       ("经验池腿", lambda q: ExpStore(embeddings=SpyEmb(), user_id="u38",
                                                       store=NoStore()).search("RunCode", q))):
        sent.clear()
        asyncio.run(run(long_q))
        assert sent == [limit], f"t38失效（{label}）：超窗 query 实发 {sent} 字，要恰好 {limit}"
        sent.clear()
        asyncio.run(run(short_q))
        assert sent == [len(short_q)], \
            f"t38失效（{label}）：短 query 被改了（实发 {sent}，要原样 {len(short_q)}）"
    print(f"  ok  t38 两条召回腿实发给端点的 query 都截到 {limit} 字上限、短问句原样通过")


def main():
    checks = [t1_redis_roundtrip_and_expiry,
 t2_redis_down_degrades_to_none,
              t3_brain_dumps_loads_only_when_dirty, t4_overflow_uses_memory_overflow_size,
              t5_summarize_rolls_history_into_summary_and_persists,
              t6_split_texts_overlaps_and_multiwindow_reduces,
              t7_tool_results_feed_next_round_prompt, t8_no_brain_windows_at_prompt_time,
              t9_brain_overflow_summarizes_and_restores,
              t10_memory_keys_are_per_role_and_per_session,
              t11_observe_dedupes_and_survives_partial_results,
              t12_collection_shape, t13_tenant_and_doctype_isolation,
              t14_sparse_indices_are_process_stable, t15_longterm_overflow_recall_roundtrip,
              t16_rolezero_uses_longterm_recall, t17_embedding_outage_degrades_not_crashes,
              t18_hitrate_single_vs_hybrid_table,
              t19_exp_schema_roundtrip, t20_serializer_think_roundtrip,
              t21_hit_count_reorders, t22_exp_cache_semantics,
              t23_exp_store_replay_on_qdrant, t24_rolezero_think_wired,
              t25_real_bge_semantic_path, t26_plan_state_machine_wired,
              t27_di_review_gate_blocks_until_resume, t28_ltm_rerank_absorbed_and_degrades,
              t29_scorer_template_verbatim, t30_simple_scorer_fake_llm_path,
              t31_exp_tenant_isolation, t32_rerank_unset_default_skips_cleanly,
              t33_point_id_carries_tenant_and_doc_type,
              t34_recall_floor_dense_score, t35_recall_floor_dense_rank,
              t36_recall_floor_rerank_score, t37_rerank_endpoint_real_answer,
              t38_both_recall_legs_clamp_their_query]
    if not live_redis():
        print("⚠ 没连上 Redis：依赖它的组会跳过，降级路径（t2）仍会验。Redis 是可选依赖。")
    if not live_qdrant():
        print(f"⚠ 没连上 Qdrant({settings.qdrant.url})：t12/t13/t15–t18/t23/t25 跳过。"
              f"R9 这层的门禁必须起容器跑一次才算数。")
    if not live_embedding():
        print(f"⚠ 真 embedding 不在线（{settings.embedding.base_url} / {settings.embedding.model}）："
              f"t25 跳过——语义路径的门禁要连 ollama 跑一次才算数。")
    for fn in checks:
        fn()
    try:
        sync_redis.Redis(host=settings.redis.host, port=settings.redis.port,
                         db=settings.redis.db).delete(*KEYS)
    except Exception as e:
        print(f"  （清理 Redis key 失败，不影响结论：{type(e).__name__}）")
    if live_qdrant():
        try:
            asyncio.run(gate_store().drop())        # 自测集合不留残余
        except Exception as e:
            print(f"  （清理 {GATE_COLL} 集合失败：{type(e).__name__}）")
    print(f"\nS5 门禁通过：{len(checks)} 组 —— S5.3 经验池与真语义 7 组（Experience 逐字段 roundtrip/"
          f"think 载荷无损往返与裁剪键/命中计数改变排序/@exp_cache 开关矩阵/"
          f"真 Qdrant+Redis 存取回放/RoleZero 接线命中零模型调用）"
          f"+ S9.2 scorer 2 组（打分模板逐字/FakeLLM 打分路径）"
          f"+ S5.1 记忆 11 组（Redis 真往返与死端口降级/"
          f"BrainMemory dirty 才写盘、溢出判定有读者、分窗摘要落盘/RoleZero 结果回喂、"
          f"截窗、摘要可恢复、key 按会话+角色隔离、去重容错）"
          f"+ S5.2 R9 检索 7 组（集合形态 named+sparse+INT8+租户索引/单集合双向隔离/"
          f"sparse 下标跨进程稳定/长期记忆入库幂等与召回字段/召回真进 prompt/"
          f"embedding 下线只降级/hit-rate@5 对照表存盘）")


if __name__ == "__main__":
    sys.exit(main())
