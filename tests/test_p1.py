"""P1 批次自测：repo_parser / plan_and_act / reflection / llm_repair_json / 边缘 Action / skills。"""
import asyncio, json
from pathlib import Path
from codeharness.provider.fake import FakeLLM
from codeharness.provider.repair import llm_repair_json, repair_llm_raw_output
from codeharness.reflection import detect_repeated_error
from codeharness.repo_parser import parse_file
from codeharness.schema import Message
from codeharness.runtime import CURRENT_PROJECT
from pydantic import BaseModel


async def main():
    CURRENT_PROJECT.set("p1_test")

    # ① repo_parser
    sample = Path("workspace/p1_test/sample.py")
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("class Foo:\n    def bar(self):\n        return 1\n\ndef top():\n    return 2\n",
                      encoding="utf-8")
    chunks = parse_file(str(sample))
    assert {c.type for c in chunks} == {"class", "function"} and len(chunks) == 2, chunks

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

    # ⑤ 边缘 Action 六件
    from codeharness.actions.edge_actions import (WriteDocstring, WriteDesignReview, WriteReview,
                                                  ExtractReadMe, AnalyzeRequirements, GenerateQuestions)
    edge_llm = FakeLLM([
        "```python\ndef add(a, b):\n    \"\"\"求和。\"\"\"\n    return a + b\n```",
        "设计评审：可行",
        "总评：良好",
        json.dumps({"summary": "示例库", "installation": "pip install", "configuration": "无", "usages": "import"}),
        json.dumps({"analysis": ["需要登录", "需要支付"]}),
        json.dumps({"questions": ["支持微信支付吗?"]}),
    ])
    assert "求和" in (await WriteDocstring(llm=edge_llm).run(Message("def add(a, b):\n    return a + b"))).content
    assert "可行" in (await WriteDesignReview(llm=edge_llm).run(Message("设计文档"))).content
    assert "良好" in (await WriteReview(llm=edge_llm).run(Message("内容"))).content
    rm = await ExtractReadMe(llm=edge_llm).run(Message("# README\n示例"))
    assert rm.instruct_content["installation"] == "pip install"
    ar = await AnalyzeRequirements(llm=edge_llm).run(Message("做一个商城"))
    assert "支付" in ar.content
    gq = await GenerateQuestions(llm=edge_llm).run(Message("讨论记录"))
    assert "微信支付" in gq.content

    # ⑥ skills 等价包
    from codeharness.skills.loader import SkillAction
    sk = SkillAction("summarize", llm=FakeLLM(["摘要完成"]))
    assert "摘要完成" in (await sk.run(Message("长文内容"))).content

    print("P1 批次自测全部通过：repo_parser/plan_and_act/reflection/llm_repair/边缘6件/skills")

asyncio.run(main())
