"""路线二开工前的**形状现取**（零花费）：标准模式真图跑起来时，「一个角色开始干活 / 收工」在
`astream_events(v2)` 里到底长什么样——有哪些 event、`name` 是什么、`metadata` 里 `langgraph_node`
与 `checkpoint_ns` 各是什么、`run_id` 能不能当这一趟执行的标识。

为什么先跑这个再写代码：本仓在这件事上应验过两次——`s17` 当年把 `on_interrupt` 当事件源，
实测 langgraph 1.2.11 的 `astream_events(v2)` 只发 `on_chain_*`，那条分支**从不触发**（A4 那批现证），
于是「停在待人工」被写成 finished；`manual_stream_landing.py` 文件头又记着「形状猜错两次、判据两次都绿」。
角色生命周期事件要挂在节点级信号上，而节点级信号的真实形状只能现取，不能照参照系的
`run_started/run_completed` 想象。

跑法（同 PLAN §5，桩在本机、不发一次真请求）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -u -B tests/manual_role_node_shape_probe.py
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "logs")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 一笔「想完就收」的合法回包：不派活、不调工具，事件序列里就只剩节点级信号，读出来干净。
END_ONLY = json.dumps({"thought": "这一轮没有要做的，直接结束。",
                       "commands": [{"command_name": "end", "args": {}}]}, ensure_ascii=False)


async def main() -> int:
    import tempfile
    import codeharness.team as T
    from codeharness.provider.cost import CostManager
    from codeharness.provider.fake import FakeLLM
    from server.events import SessionEventBus
    from server.runner import SessionRunner
    from server.sessions import SessionStore

    T._make_llm = lambda cost_manager=None, override=None: FakeLLM([END_ONLY])

    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    s = store.create("形状现取：写一个解释二分查找的静态网页", project_name="role_node_shape",
                     paradigm="classic", n_round=1, permission="readonly")
    runner.costs[s.id] = CostManager()

    team, config, init = await runner._prepare(s, s.project_name or s.id, runner.costs[s.id])
    print(f"[装配] paradigm={s.paradigm} roles={list(getattr(s, 'roles', None) or [])} "
          f"entry={getattr(s, 'entry_role', '')}")

    kinds: Counter = Counter()
    node_rows = []
    async for ev in team.astream_events(init, config, version="v2"):
        kind = ev.get("event", "")
        kinds[kind] += 1
        md = ev.get("metadata") or {}
        node = md.get("langgraph_node", "")
        ns = str(md.get("checkpoint_ns", ""))
        if node or kind.startswith("on_chain_start"):
            node_rows.append((kind, str(ev.get("name", "")), str(node), ns[:34],
                              str(ev.get("run_id", ""))[:8],
                              ",".join(t for t in (ev.get("tags") or []) if not t.startswith("seq:"))[:24]))

    print("\n[A] 事件种类计数（真图 + 真 astream_events v2）")
    for k, v in kinds.most_common():
        print(f"    {k:<26} {v}")

    print(f"\n[B] 节点级样本（共 {len(node_rows)} 行，全打）：event | name | langgraph_node | checkpoint_ns | run_id | tags")
    for r in node_rows:
        print("    " + " | ".join(r))

    # [C] 我要的三条结论，直接从上面读，别让下一棒再猜一遍
    starts = [r for r in node_rows if r[0] == "on_chain_start"]
    ends = [r for r in node_rows if r[0] == "on_chain_end"]
    print("\n[C] 三条判据（决定角色生命周期事件挂在哪）")
    print(f"    ① on_chain_start 的 name 取值集合：{sorted({r[1] for r in starts})}")
    print(f"    ② 同 name 的 start/end 是否配对：start={len(starts)} end={len(ends)}")
    print(f"    ③ 角色名有没有出现在 langgraph_node 里（有＝节点级信号可直接用；"
          f"没有＝只能靠 checkpoint_ns 首段或报道槽的 owner）："
          f"{sorted({r[2] for r in node_rows if r[2]})}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
