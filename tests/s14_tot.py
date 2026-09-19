#!/usr/bin/env python -m asyncio
"""批次1: ToT 树搜索门禁（照源移植 BFS/DFS + RoleZero plan_fn）。

关键：证明上轮两个 bug 已修——① evaluate 现在真把分数写回 node.value（旧代码 evaluate() 返回值
丢弃，best_leaf 恒取首支、择优是假的）；② 无 n.id AttributeError。用按 node.id 打分的确定性
evaluator 绕开 asyncio.gather 并发乱序，让"选高分支"可断言。真模型通道 manual_tot.py。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.provider.fake import FakeLLM
from codeharness.strategy.base import ThoughtNode, ThoughtTree
from codeharness.strategy.tot import (BFSSolver, DFSSolver, SimpleParser, ThoughtSolverConfig,
                                      MethodSelect, make_tot_planner)

NODES2 = '```json\n[{"node_id":"1","node_state_instruction":"branchA"},{"node_id":"2","node_state_instruction":"branchB"}]\n```'


class IdEvaluator:
    """按 node_id 打分：id==1→9（高分），否则 1。确定性与 gather 顺序无关。"""
    threshold = 1

    def __call__(self, text, node_id=None, **kw):
        return 9.0 if str(node_id) == "1" else 1.0

    def status_verify(self, value):
        return value >= self.threshold


def _solver(responses, n_gen=2, n_sel=3, max_steps=1):
    cfg = ThoughtSolverConfig(max_steps=max_steps, method_select=MethodSelect.GREEDY,
                              n_generate_sample=n_gen, n_select_sample=n_sel,
                              parser=SimpleParser(), evaluator=IdEvaluator())
    return BFSSolver(llm=FakeLLM(responses), config=cfg)


def t1_generate_builds_children():
    print("t1: generate_thoughts...", end=" ", flush=True)
    s = _solver([NODES2])
    root = ThoughtNode("Goal: X")
    s.thought_tree = ThoughtTree(root)
    kids = asyncio.run(s.generate_thoughts("Goal: X", current_node=root))
    assert len(kids) == 2, f"应生成 2 子节点，实际{len(kids)}"
    assert {k.name for k in kids} == {"branchA", "branchB"}, [k.name for k in kids]
    assert len(root.children) == 2, "子节点应挂到 root 上"
    print("✅")


def t2_evaluate_writes_value():
    """核心回归：evaluate_node 必须把分写回 node.value（旧代码丢弃返回值）。"""
    print("t2: evaluate writes value...", end=" ", flush=True)
    s = _solver(["irrelevant"])                       # evaluator 按 id 打分，llm 文本无关
    root = ThoughtNode("Goal: X")
    s.thought_tree = ThoughtTree(root)
    hi = ThoughtNode("branchA", parent=root, id=1)
    lo = ThoughtNode("branchB", parent=root, id=2)
    asyncio.run(s.evaluate_node(hi, parent_value=0))
    asyncio.run(s.evaluate_node(lo, parent_value=0))
    assert hi.value == 9.0, f"evaluate 未写回 value（旧 bug）：hi.value={hi.value}"
    assert lo.value == 1.0, f"lo.value={lo.value}"
    assert hi.valid_status and lo.valid_status
    # 累计分：parent_value 要加进来（源语义）
    n = ThoughtNode("c", parent=root, id=1)
    asyncio.run(s.evaluate_node(n, parent_value=5))
    assert n.value == 14.0, f"累计分不对（parent+value）：{n.value}"
    print("✅")


def t3_select_keeps_highest():
    print("t3: select_nodes greedy...", end=" ", flush=True)
    s = _solver(["x"], n_sel=1)
    root = ThoughtNode("Goal")
    s.thought_tree = ThoughtTree(root)
    hi = ThoughtNode("branchA", parent=root, id=1); hi.update_value(9)
    lo = ThoughtNode("branchB", parent=root, id=2); lo.update_value(1)
    kept = s.select_nodes([hi, lo])
    assert kept == [hi], f"应留高分支，实际{[k.name for k in kept]}"
    assert root.children == [hi], "低分枝应从树上摘除（否则仍被搜到）"
    print("✅")


def t4_bfs_solve_picks_high_path():
    print("t4: BFSSolver.solve...", end=" ", flush=True)
    s = _solver([NODES2, "x"], n_gen=2, n_sel=3, max_steps=1)   # 1 generate + 2 evaluate
    path = asyncio.run(s.solve("Goal: solve-it"))
    assert path and path[-1] == "branchA", f"应选到高分支 branchA，实际路径={path}"
    assert path[0] == "Goal: solve-it", "路径首元素应是根"
    print("✅")


def t5_plan_fn_on_rolezero():
    """registry.build_role(strategy=tot) 挂的是 plan_fn（真钩子），非旧的空挂 _plan；且 plan_fn(goal) 可用。"""
    print("t5: plan_fn wiring...", end=" ", flush=True)
    from codeharness.roles.registry import build_role
    llm = FakeLLM([NODES2, "x"])
    role = build_role(name="TeamLeader", llm=llm, strategy="tot")
    assert getattr(role, "plan_fn", None) is not None and callable(role.plan_fn), "plan_fn 未接"
    assert not hasattr(role, "_plan"), "旧的空挂 _plan 属性不应存在"
    assert role.profile.get("strategy") == "tot"
    out = asyncio.run(role.plan_fn("做个 CLI 工具"))
    # 本组只验 plan_fn 真钩子接入 + 可调用产 str（择优正确性由 t2/t4 覆盖）
    assert isinstance(out, str) and "做个 CLI 工具" in out, f"plan_fn 应产出含 goal 的路径文本，实际={out!r}"
    print("✅")


def t6_dfs_runs():
    print("t6: DFSSolver basic...", end=" ", flush=True)
    cfg = ThoughtSolverConfig(max_steps=2, n_generate_sample=2, n_select_sample=1,
                              parser=SimpleParser(), evaluator=IdEvaluator())
    s = DFSSolver(llm=FakeLLM([NODES2, "x"]), config=cfg)
    path = asyncio.run(s.solve("Goal: dfs"))
    assert isinstance(path, list) and len(path) >= 1, f"DFS 应返回路径，实际={path}"
    print("✅")


def main():
    print("=" * 60)
    print("批次1: ToT 树搜索门禁")
    print("=" * 60)
    checks = [t1_generate_builds_children, t2_evaluate_writes_value, t3_select_keeps_highest,
              t4_bfs_solve_picks_high_path, t5_plan_fn_on_rolezero, t6_dfs_runs]
    for c in checks:
        c()
    print("\n" + "=" * 60 + f"\n✅ 全部通过 ({len(checks)}/{len(checks)})\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
