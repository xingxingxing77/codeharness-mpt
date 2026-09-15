"""S8 门禁（第一件）：runner 计量合流——还债清单 #1 的账本这条链。

钉四件事（2026-09-15 真模型首跑实测的三个洞）：
  t1  add_usage 拿不到 usage 时**必须留 warning**（静默记 0 让一场会话几十次调用只有零星入账，
      现场无从分辨哪条路没回执）；两种标准形态（usage_metadata / token_usage）照常计。
  t2  重启后 resume 的账本从 sessions.json 快照续算，不从 0 起（否则终态覆盖历史用量）。
  t3  跑动中每一笔 on_chat_model_end 都把账本合进 store + 落盘 + SSE status 事件——
      「跑动中 GET /sessions/{sid} 的 cost 恒 0」到此为止。断言打在**同一个 CostManager 实例**上。
  t4  _ensure_graph 重建路径：传给 prepare_project 的 cost_manager 必须就是 runner.costs[sid] 那个
      实例（双账本防回归，docs 第 0 步的教训）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s8_runner_meter.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from codeharness.provider.cost import CostManager
from server.runner import SessionRunner, _seeded_ledger
from server.events import SessionEventBus
from server.sessions import SessionStore, SessionStatus


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


class _RecLogger:
    """替掉 codeharness.provider.cost.logger：断言打在「有没有留这条 warning」上。"""

    def __init__(self):
        self.warnings, self.infos = [], []

    def warning(self, m):
        self.warnings.append(str(m))

    def info(self, m):
        self.infos.append(str(m))


def t1_add_usage_visible():
    import codeharness.provider.cost as cost_mod
    saved, rec = cost_mod.logger, _RecLogger()
    cost_mod.logger = rec
    try:
        cm = CostManager()
        m1 = AIMessage(content="x")
        m1.usage_metadata = {"input_tokens": 120, "output_tokens": 30}
        cm.add_usage(m1, model="gpt-4o", tag="a")                    # 标准口径（流式也走这条）
        m2 = AIMessage(content="y", response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        cm.add_usage(m2, model="gpt-4o", tag="b")                    # OpenAI 原始口径
        m3 = AIMessage(content="z")
        cm.add_usage(m3, model="gpt-4o", tag="silent-leak")          # 两种都没有 → 必须留话
        assert cm.total_prompt_tokens == 127 and cm.total_completion_tokens == 33, cm.get_costs()
        assert any("silent-leak" in w for w in rec.warnings), f"零用量没留 warning: {rec.warnings}"
        _ok("t1", "add_usage：两形态照常计，零用量漏账留 warning（tag 可 grep）")
    finally:
        cost_mod.logger = saved


def t2_seeded_ledger():
    cm = _seeded_ledger({"total_prompt_tokens": 490, "total_completion_tokens": 21, "total_cost": 0.0123})
    assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.total_cost) == (490, 21, 0.0123)
    empty = _seeded_ledger({})
    assert empty.total_prompt_tokens == 0
    broken = _seeded_ledger({"total_prompt_tokens": None, "total_cost": ""})
    assert (broken.total_prompt_tokens, broken.total_cost) == (0, 0.0)
    _ok("t2", "_seeded_ledger 从落盘快照续算，缺项/坏项退 0 不炸")


async def _make_runner():
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    s = store.create("计量冒烟", project_name="meter_proj")
    store.update(s.id, status=SessionStatus.running)
    return tmp, store, bus, runner, s


async def t3_midrun_sync():
    tmp, store, bus, runner, s = await _make_runner()
    cm = CostManager()
    runner.costs[s.id] = cm
    runner.projects[s.id] = "meter_proj"
    cm.update_cost(3000, 150, "gpt-4o")
    runner._translate(s.id, {"event": "on_chat_model_end"})          # 模拟网关完成一笔
    got = store.get(s.id).cost
    assert got["total_prompt_tokens"] == 3000 and got["total_completion_tokens"] == 150, got
    disk = json.loads((tmp / "sessions.json").read_text(encoding="utf-8"))
    assert [d for d in disk if d["id"] == s.id][0]["cost"]["total_prompt_tokens"] == 3000, "没落盘"
    evs = [e for e in bus.history(s.id) if e.kind == "status"]
    assert evs and evs[-1].value["cost"]["total_prompt_tokens"] == 3000, "SSE 里没有增量"
    assert evs[-1].value["status"] == "running", "usage 事件不得改状态"
    cm.update_cost(1000, 60, "gpt-4o")
    runner._translate(s.id, {"event": "on_chat_model_end"})
    assert store.get(s.id).cost["total_prompt_tokens"] == 4000       # 合流是累计不是覆盖
    runner.costs.pop(s.id)                                           # _forget 之后再来事件
    runner._translate(s.id, {"event": "on_chat_model_end"})          # 不许炸
    _ok("t3", "跑动中每笔 LLM 结束：GET/落盘/SSE 三头同步，账累计，散会后到迟事件不炸")


async def t4_ensure_graph_single_ledger():
    tmp, store, bus, runner, s = await _make_runner()
    store.update(s.id, status=SessionStatus.awaiting_human,
                 cost={"total_prompt_tokens": 490, "total_completion_tokens": 21, "total_cost": 0.05})
    captured = {}

    def fake_prepare(idea, project, checkpointer=None, cost_manager=None):
        captured["cost_manager"] = cost_manager
        return object(), {"configurable": {"thread_id": project}}, None

    async def fake_saver():
        return None

    import codeharness.team as team
    saved, runner._saver = team.prepare_project, fake_saver
    team.prepare_project = fake_prepare
    try:
        packed = await runner._ensure_graph(s.id)
        assert packed is not None
        cm = runner.costs[s.id]
        assert captured["cost_manager"] is cm, "双账本：传给图的实例 ≠ runner 手上那个"
        assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.total_cost) == (490, 21, 0.05), \
            "重启路径没从快照续算"
        cm.update_cost(10, 2, "gpt-4o")
        assert cm.total_prompt_tokens == 500                         # 续算后累加正确
    finally:
        team.prepare_project = saved
    _ok("t4", "_ensure_graph 重建路径：单一账本实例传进图，重启后从落盘快照续算")


def main():
    t1_add_usage_visible()
    t2_seeded_ledger()
    asyncio.run(t3_midrun_sync())
    asyncio.run(t4_ensure_graph_single_ledger())
    print("\ns8_runner_meter: 4/4 全绿")


if __name__ == "__main__":
    main()
