"""全量角色注册表自测：18 个角色逐一实例化 + 经典线角色带剧本实跑。"""
import asyncio, json
from codeharness.provider.fake import FakeLLM
from codeharness.roles.registry import ALL_ROLES, build_role
from codeharness.schema import Message
from codeharness.runtime import CURRENT_PROJECT

EXPECTED = {"TeamLeader", "ProductManager", "Architect", "ProjectManager", "Engineer2",
            "Assistant", "SweAgent", "Engineer", "QaEngineer", "Researcher", "Searcher",
            "Sales", "CustomerService", "Teacher", "TutorialAssistant",
            "InvoiceOCRAssistant", "DataAnalyst", "DataInterpreter"}


async def main():
    assert set(ALL_ROLES) == EXPECTED, set(ALL_ROLES) ^ EXPECTED

    # ① 全部实例化 + 可编译
    llm = FakeLLM(["x"])
    built = {name: build_role(name, llm) for name in ALL_ROLES}
    for name, role in built.items():
        role.build() if hasattr(role, "build") and callable(getattr(role, "build", None)) else None
    assert all(hasattr(r, "as_node") for r in built.values())

    # ② Sales：源 desc 原则进入 prefix（前缀含 "Retail Sales Guide" 与知识库声明）
    s = built["Sales"]
    assert "Retail Sales Guide" in s.build_prefix() and "knowledge base" in s.build_prefix()
    # ③ CustomerService：DESC 五原则逐字在前缀里
    cs_prefix = built["CustomerService"].build_prefix()
    assert "never ask the customer for the order number" in cs_prefix

    # ④ Researcher 实跑（search 降级路径）
    CURRENT_PROJECT.set("roles_test")
    res = await built["Researcher"].build().ainvoke(
        {"name": "David", "inbox": [Message("调研多智能体框架", cause_by="UserRequirement")],
         "memory": [], "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
    assert res["output"] and res["output"][0].cause_by == "Research"

    # ⑤ DataAnalyst 实跑（写分析代码 → 沙箱真执行）
    da_llm = FakeLLM(["```python\nprint('sum =', 1 + 2)\n```"])
    da = build_role("DataAnalyst", da_llm)
    r = await da.build().ainvoke({"name": "David", "inbox": [Message("计算 1+2")],
                                  "memory": [], "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
    out_txt = "\n".join(m.content for m in r["output"])
    assert "sum = 3" in out_txt, out_txt

    # ⑥ TutorialAssistant 两段（目录 → 逐节正文）
    tu_llm = FakeLLM([
        json.dumps({"title": "LangGraph 入门",
                    "sections": [{"index": 1, "title": "核心概念", "description": "图与节点"},
                                 {"index": 2, "title": "第一个图", "description": "hello world"}]}),
        "Graph 是编排单位...", "StateGraph 用法...",
    ])
    tu = build_role("TutorialAssistant", tu_llm)
    r2 = await tu.build().ainvoke({"name": "Stitch", "inbox": [Message("写一个 LangGraph 教程")],
                                   "memory": [], "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
    final = r2["output"][-1].content
    assert "教程已完成" in final
    from pathlib import Path
    assert (Path("workspace/roles_test/resources/tutorial.md")).exists()

    # ⑦ InvoiceOCR 未配置 provider 的明确提示路径
    io_llm = FakeLLM(["x"])
    io = build_role("InvoiceOCRAssistant", io_llm)
    r3 = await io.build().ainvoke({"name": "Iris", "inbox": [Message("invoice.png")],
                                   "memory": [], "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
    assert "未配置 OCR 服务" in r3["output"][0].content

    # ⑧ 批次3：源专属 instruction/constraints/example 真接进实例（非 grep，读装配后的对象）
    il = FakeLLM(["x"])
    pm = build_role("ProductManager", il)
    ar = build_role("Architect", il)
    en2 = build_role("Engineer2", il)
    pj = build_role("ProjectManager", il)
    sw = build_role("SweAgent", il)
    tl = build_role("TeamLeader", il)
    from codeharness.prompts.product_manager import PRODUCT_MANAGER_INSTRUCTION
    from codeharness.prompts.di.architect import ARCHITECT_INSTRUCTION, ARCHITECT_EXAMPLE
    from codeharness.prompts.di.engineer2 import ENGINEER2_INSTRUCTION
    from codeharness.prompts.di.swe_agent import NEXT_STEP_TEMPLATE
    assert pm.instruction == PRODUCT_MANAGER_INSTRUCTION, "PM 未接源 instruction"
    assert ar.instruction == ARCHITECT_INSTRUCTION and ar.example == ARCHITECT_EXAMPLE, "Architect instruction/example 未接"
    assert en2.instruction == ENGINEER2_INSTRUCTION, "Engineer2 未接源 instruction"
    assert pj.instruction == "Use WriteTasks tool to write a project task list", "ProjectManager 未接源一句话"
    assert sw.instruction == NEXT_STEP_TEMPLATE, "SweAgent 未回接源 NEXT_STEP_TEMPLATE"
    # constraints 进 _prefix（源 PM/Architect 皆有 constraints → Role._get_prefix；两引擎拉平）
    assert "The constraint is" in pm._prefix() and "same language" in pm._prefix(), "PM constraints 没进 prefix"
    assert "The constraint is" in ar._prefix(), "Architect constraints 没进 prefix"
    # TeamLeader 走 provider 钩子（源每轮 format team_info），此处证明 provider 生效且非静态默认
    assert callable(tl.instruction_provider), "TL 未接 instruction_provider"
    from codeharness.prompts.di.team_leader import TL_INSTRUCTION
    assert tl.instruction_provider() == TL_INSTRUCTION.format(team_info=""), "TL provider 产物不对"

    print(f"角色注册表自测通过：{len(ALL_ROLES)} 个角色实例化 + Researcher/DataAnalyst/Tutorial/InvoiceOCR 实跑"
          f" + 批次3 instruction/constraints/example 接线⑧组")

asyncio.run(main())
