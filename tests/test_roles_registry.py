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

    print(f"角色注册表自测通过：{len(ALL_ROLES)} 个角色实例化 + Researcher/DataAnalyst/Tutorial/InvoiceOCR 实跑")

asyncio.run(main())
