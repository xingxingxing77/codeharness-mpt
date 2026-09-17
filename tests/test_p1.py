"""P1 批次自测：plan_and_act / reflection / llm_repair_json / 边缘 Action / skills。

（原 ① repo_parser CodeChunk 切分冒烟随接线台账 #18 终判删除——2026-09-18：预留面至 S9 无生产
消费者，按预登记规则"届时不接随删"整面摘除；RAG 检索质量门禁走 s9_benchmark，不走代码切分。）"""
import asyncio, json
from pathlib import Path
from codeharness.provider.fake import FakeLLM
from codeharness.provider.repair import llm_repair_json, repair_llm_raw_output
from codeharness.reflection import detect_repeated_error
from codeharness.schema import Message
from codeharness.runtime import CURRENT_PROJECT
from pydantic import BaseModel


async def main():
    CURRENT_PROJECT.set("p1_test")

    # ② plan_and_act：计划 → 写代码 → 沙箱执行 → 汇总
    from codeharness.roles.registry import build_role
    di_llm = FakeLLM([
        json.dumps({"goal": "算 1+2", "tasks": [
            {"task_id": "T1", "dependent_task_ids": [], "instruction": "写脚本计算 1+2 并打印",
             "assignee": "David"}]}),
        "```python\nprint('sum =', 1 + 2)\n```",
        "结论：1+2=3",
    ])
    di = build_role("DataInterpreter", di_llm)
    sub_run = di.as_node("DataInterpreter")[1]
    async def run_with_cfg(state):
        import langgraph.config as _c
        from langchain_core.runnables import ensure_config
        cfg = ensure_config({"configurable": {"thread_id": "p1_di"}})
        tok = _c.var_child_runnable_config.set(cfg)
        try:
            return await sub_run(state)
        finally:
            _c.var_child_runnable_config.reset(tok)
    r = await run_with_cfg({"_inbox": [Message("分析 1+2")]})
    assert "结论" in r["messages"][0].content, r["messages"][0].content

    # ③ reflection：重复错误检测 + reflect 产出
    from codeharness.reflection import reflect, detect_repeated_error
    hist = [{"thought": "t1", "commands": [], "results": [{"name": "x", "result": "[错误] TimeoutError: 失败"}]},
            {"thought": "t2", "commands": [], "results": [{"name": "x", "result": "[错误] TimeoutError: 又失败"}]}]
    det = detect_repeated_error(hist)
    assert det and det.startswith("[错误]") and "TimeoutError" in det, det
    assert detect_repeated_error([hist[0]]) is None          # 单轮不触发
    ref_llm = FakeLLM(["换一种写法"])
    advice = await reflect(ref_llm, "任务", hist, "TimeoutError")
    assert "Self-Reflection" in advice

    # ④ llm_repair_json（structured 失败后的 LLM 自修档）
    class Out(BaseModel):
        thought: str
    broken_llm = FakeLLM(["```json\n{\"thought\": \"fixed\"}\n```"])
    fixed = await llm_repair_json('{"thought": "fixed",}', Out, broken_llm)
    assert fixed.thought == "fixed"

    # ⑤ ExtractReadMe（原"边缘六件"里其余五只 2026-09-16 对账后删除——源侧零消费者，
    #   只留唯一有真实消费链的这只；B5 起为源四段式：system 逐字 + 四谓词入 SPO 图，台账 #13）
    from codeharness.actions.edge_actions import ExtractReadMe
    from codeharness.const import GRAPH_REPO_FILE_REPO
    edge_llm = FakeLLM(["示例库：做加法", "```bash\ngit clone … && pip install .\n```", "```bash\n```\n",
                        "```python\nimport demo\n```"])
    rm = await ExtractReadMe(llm=edge_llm).run(
        Message(content="# README\n示例库", instruct_content={"repo": "demo"}, instruct_schema="ReadmeSummary"))
    assert len(edge_llm.calls) == 4, f"源面是四段 aask，实为 {len(edge_llm.calls)}"
    assert rm.instruct_content["summary"].startswith("示例库")
    graph_json = Path("workspace/p1_test") / GRAPH_REPO_FILE_REPO / "readme.json"
    assert graph_json.exists(), "四要素没入 SPO 图"
    spo = graph_json.read_text(encoding="utf-8")
    for pred in ("has_summary", "has_install", "has_config", "has_usage"):
        assert pred in spo, f"图里缺谓词 {pred}"

    # ⑥ skills 等价包
    from codeharness.skills.loader import SkillAction
    sk = SkillAction("summarize", llm=FakeLLM(["摘要完成"]))
    assert "摘要完成" in (await sk.run(Message("长文内容"))).content

    print("P1 批次自测全部通过：repo_parser/plan_and_act/reflection/llm_repair/ExtractReadMe入图/skills")

asyncio.run(main())
