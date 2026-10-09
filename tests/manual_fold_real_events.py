"""P4 路线三（10-09）的真事件工装——零花费，喂给前端 reducer 的是**发射点产出的原样事件**。

为什么还要这一份：`frontend/scripts/fold_check.ts` 里那些事件是我手搭的。本仓在这件事上应验过两次，
账写在 `tests/manual_stream_landing.py` 文件头：「形状猜错两次、真会话里 `_prompt_text` 抽到空串、
回显排除静默空转，判据两次都绿」。所以这条把真 `astream_events` + 真 `SessionRunner._translate`
+ 真报道槽（`codeharness/report.py::_emit`）产出的事件序列落到磁盘，交给**同一份 reducer**按三条路折：
  · 逐条增量（活流的形状）
  · 整本一次（首屏 / 重连）
  · 先只看到尾屏 400 条、再前插整本重折（「加载更早」，`store::replayLog` 走的就是这条）
三条终态必须逐字段相同；且 `unhandled` 必须为空——后一条才是这张处置表真正的验收：
**真流里出现的每一个 kind/name 都在表里**，不是「我只把自己的例子放进表」。

桩直接复用 `manual_stream_landing` 那台（含它的 `run_case`：真 structured 调用 + 真逐片抽取），
所以这里不重抄发射形状。零花费：请求只打到 127.0.0.1 的桩。

跑法（同 PLAN §5 那一串）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -u -B tests/manual_fold_real_events.py
产物：`E:/tmp/fold_events.json` + 直方图读数 + node 侧三条路对账的 exit 码。
"""
import asyncio
import json
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "logs"), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT = Path("E:/tmp/fold_events.json")
ECHO = "做一个极小的静态网页，用一段话解释二分查找是什么。不要写测试，不要部署。"


async def main() -> int:
    import manual_stream_landing as msl
    from langchain_core.messages import HumanMessage, SystemMessage
    from codeharness.actions.write_prd import PRDOutput
    from codeharness.actions.project_management import TaskList
    from codeharness.roles.role_zero import ZeroThought

    srv = HTTPServer(("127.0.0.1", 0), msl.Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    msl.BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    msl.STUB_DELAY = 0.004            # 让逐片真的一点点到（快档一次吐完会少几条 live 事件）

    store, bus, runner, s = msl._env()   # 一份真 store/bus/runner，所有 case 灌进同一场
    print(f"[桩] {msl.BASE_URL} · 会话 {s.id}")

    prd_msgs = [SystemMessage(content="你是产品经理，按 schema 输出 json。"),
                HumanMessage(content="项目：binary_page\n用户需求：" + ECHO + "\n请写 PRD。")]
    think_msgs = [SystemMessage(content="按 schema 输出"),
                  HumanMessage(content="用两三句话解释二分查找，说完结束。")]
    task_msgs = [SystemMessage(content="按 schema 输出任务清单。"),
                 HumanMessage(content="把二分查找那个页面拆成两条任务：" + ECHO)]

    # 六笔：三种块（Docs 开块 / Task 开块 / 无内核块走兜底行）× 两种角色归属，
    # 让 meta/live/content/end_marker 四条通道与 `stream-*` 兜底行都在同一条流里出现过。
    for i in range(3):
        await msl.run_case(f"PRD→Docs #{i}", PRDOutput, prd_msgs,
                           open_block_uuid=f"doc-{i}", prose_fields=PRDOutput.prose_fields,
                           env=(store, bus, runner, s), forget=False)
        await msl.run_case(f"TaskList→Task #{i}", TaskList, task_msgs,
                           open_block_uuid=f"task-{i}", block="Task",
                           prose_fields=getattr(TaskList, "prose_fields", None),
                           env=(store, bus, runner, s), forget=False)
        await msl.run_case(f"ZeroThought→兜底行 #{i}", ZeroThought, think_msgs,
                           env=(store, bus, runner, s), forget=False)
    # 收口 + 轮尾两类（runner 的正常路径自己会发，这里补一次散会清扫，让兜底行都有 end_marker）
    runner._end_stream_rows(s.id)
    runner.bus.publish(s.id, kind="turn", name="end", value={"reason": {"kind": "max-tokens"}})
    runner.bus.publish(s.id, kind="status", value={"status": "finished", "error": "", "cost": {}, "message": ""})

    evs = [e.model_dump() for e in bus.history(s.id)]
    OUT.write_text(json.dumps(evs, ensure_ascii=False), encoding="utf-8")

    kinds: dict = {}
    for e in evs:
        v = e.get("value")
        rk = v.get("reason", {}).get("kind") if isinstance(v, dict) else None
        key = f"turn:{rk}" if e["kind"] == "turn" and rk else (f"{e['kind']}:{e['name']}" if e.get("name") else e["kind"])
        kinds[key] = kinds.get(key, 0) + 1
    blocks: dict = {}
    for e in evs:
        if e["kind"] == "report" and e.get("block"):
            blocks[e["block"]] = blocks.get(e["block"], 0) + 1
    print(f"[事件] 共 {len(evs)} 条 · kind/name 直方图：")
    for k, v in sorted(kinds.items()):
        print(f"    {k:<26} {v}")
    print(f"[块型] {blocks}")
    print(f"[落盘] {OUT}")
    if not blocks.get("Thought"):
        print("[红] 连兜底行/思考块都没发出来——这台工装没真的跑过发射，下面的对账会是空转（空转不算绿）")
        return 1
    srv.shutdown()

    r = subprocess.run(["node", str(ROOT / "frontend" / "scripts" / "fold_check.ts"), "--events", str(OUT)],
                       cwd=str(ROOT / "frontend"), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print("[stderr]", r.stderr.strip()[:900])
    return r.returncode


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
