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
import sys
import uuid

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
    raw = sync_redis.Redis(host=settings.redis.host, port=settings.redis.port).get(k)
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
    r1 = asyncio.run(role._think(s0))
    assert len(role.memory.storage) == 1                       # 只有 thought 入库
    acted = {**r1, "history": [{**r1["history"][-1],
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


def main():
    checks = [t1_redis_roundtrip_and_expiry, t2_redis_down_degrades_to_none,
              t3_brain_dumps_loads_only_when_dirty, t4_overflow_uses_memory_overflow_size,
              t5_summarize_rolls_history_into_summary_and_persists,
              t6_split_texts_overlaps_and_multiwindow_reduces,
              t7_tool_results_feed_next_round_prompt, t8_no_brain_windows_at_prompt_time,
              t9_brain_overflow_summarizes_and_restores,
              t10_memory_keys_are_per_role_and_per_session,
              t11_observe_dedupes_and_survives_partial_results]
    if not live_redis():
        print("⚠ 没连上 Redis：t1/t3/t5/t9 会跳过，降级路径（t2）仍会验。生产上 Redis 是可选依赖。")
    for fn in checks:
        fn()
    try:
        r = sync_redis.Redis(host=settings.redis.host, port=settings.redis.port)
        r.delete(*KEYS)
    except Exception as e:
        print(f"  （清理 Redis key 失败，不影响结论：{type(e).__name__}）")
    print(f"\nS5.1 门禁通过：{len(checks)} 组 —— Redis 薄壳 2 组（真往返+TTL/死端口静默降级）"
          f"+ BrainMemory 4 组（dirty 才写盘/溢出判定有读者/摘要滚动落盘/分窗带重叠）"
          f"+ RoleZero 工作记忆 5 组（工具结果回喂下一轮 prompt/无 brain 只截窗/溢出摘要可恢复/"
          f"key 按会话+角色隔离/结果去重与空 results 容错）")


if __name__ == "__main__":
    sys.exit(main())
