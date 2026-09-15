import asyncio, json
from codeharness.provider.fake import FakeLLM
from codeharness.roles.agent import Agent
from codeharness.schema import Message
from codeharness.const import RequirementTag, RepoName, DocName
from codeharness.runtime import CURRENT_PROJECT
from codeharness.environment.team_graph import build_team
from codeharness.actions.write_prd import WritePRD
from codeharness.actions.project_management import WriteTasks
from codeharness.actions.write_code import WriteCode
from codeharness.actions.summarize_code import SummarizeCode
from codeharness.actions.write_test import WriteTest
from codeharness.actions.run_code import RunCode
from codeharness.actions.prepare_documents import PrepareDocuments
from codeharness.actions.design_api import WriteDesign
from codeharness.actions.write_code_review import WriteCodeReview
from pathlib import Path

PRD = {"language": "en_us", "programming_language": "python", "original_requirements": "2048",
       "project_name": "game_2048", "product_goals": ["fun"], "user_stories": ["p"],
       "competitive_analysis": ["a"], "competitive_quadrant_chart": "q",
       "requirement_analysis": "ra", "requirement_pool": [["P0", "core"]],
       "ui_design_draft": "s", "anything_unclear": ""}

async def main():
    CURRENT_PROJECT.set("e2e_proj")
    pm = Agent({"name": "PM", "profile": "Product Manager", "goal": "PRD"},
               [PrepareDocuments(llm=FakeLLM([])), WritePRD(llm=FakeLLM([json.dumps(PRD)]))],
               FakeLLM([]), react_mode="BY_ORDER", max_loops=3, watch={"UserRequirement"})
    design_script = json.dumps({
        "implementation_approach": "用 pygame", "project_name": "game_2048",
        "file_list": ["main.py"],
        "data_structures_and_interfaces": "classDiagram\nclass Game",
        "program_call_flow": "sequenceDiagram\nGame->>UI: render()"})
    architect = Agent({"name": "Bob", "profile": "Architect", "goal": "design"},
                       [WriteDesign(llm=FakeLLM([design_script]))], FakeLLM([]),
                       max_loops=2, watch={"WritePRD"})
    tasks_script = json.dumps({"task_list": [
        {"filename": "main.py", "task_id": "1", "dependent_task_ids": [],
         "instruction": "实现 add"}]})
    pmm = Agent({"name": "PMManager", "profile": "Project Manager", "goal": "tasks"},
                [WriteTasks(llm=FakeLLM([tasks_script]))], FakeLLM([]), max_loops=2,
                watch={"WriteDesign"})
    eng_llm = FakeLLM(["```python\ndef add(a, b):\n    return a + b\n```",
                       "## Code Review Result\nLGTM",
                       "变更摘要：新增 main.py 实现 add"])
    eng = Agent({"name": "Engineer", "profile": "Engineer", "goal": "code"},
                [WriteCode(llm=eng_llm), WriteCodeReview(llm=eng_llm), SummarizeCode(llm=eng_llm)], eng_llm,
                react_mode="BY_ORDER", max_loops=5, watch={"WriteTasks"})

    sop = {RequirementTag.USER_REQUIREMENT: ["PM"],
           RequirementTag.WRITE_PRD: ["Architect"],
           RequirementTag.WRITE_DESIGN: ["PMManager"],
           RequirementTag.WRITE_TASKS: ["Engineer"]}
    g = build_team({"PM": pm, "Architect": architect, "PMManager": pmm, "Engineer": eng}, sop=sop)
    init = {"messages": [Message(content="做个2048游戏", cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0, "debug_rounds": 0, "finished": False}
    async for _ in g.astream(init, {"configurable": {"thread_id": "e2e"}}):
        pass
    root = Path("workspace/e2e_proj")
    assert (root / "docs" / "requirement.md").exists()
    assert (root / "docs" / "tasks.json").exists()
    assert (root / "src" / "main.py").exists()
    assert "add" in (root / "src" / "main.py").read_text(encoding="utf-8")
    assert (Path("workspace/game_2048") / "docs" / "prd" / "prd.json").exists()
    print("e2e ①：SOP 链 需求→PRD→任务→代码 全部落盘 ✅")

    test_code = ("```python\nimport unittest\nfrom src.main import add\n"
                 "class T(unittest.TestCase):\n    def test_add(self):\n"
                 "        self.assertEqual(add(2, 3), 5)\n```")
    qa_llm = FakeLLM([json.dumps({"thought": "写测试", "action": "WriteTest"}),
                      test_code,
                      json.dumps({"thought": "跑测试", "action": "RunCode"}),
                      json.dumps({"thought": "通过，结束", "action": "End"})])
    qa = Agent({"name": "QA", "profile": "QA Engineer", "goal": "test"},
               [WriteTest(llm=qa_llm), RunCode(llm=qa_llm)], qa_llm, react_mode="REACT", max_loops=6)
    ctx_msg = Message(content="测试 main.py", role="assistant",
                      instruct_content={"code_doc": {"filename": "main.py", "root_path": "src",
                                                     "content": (root / "src" / "main.py").read_text(encoding="utf-8")}})
    out = await qa.build().ainvoke({"name": "QA", "inbox": [ctx_msg], "memory": [],
                                    "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
    causes = [m.cause_by for m in out["output"]]
    assert "WriteTest" in causes and "RunCode" in causes, causes
    run_msg = next(m for m in out["output"] if m.cause_by == "RunCode")
    assert run_msg.instruct_content["ok"] is True, run_msg
    assert (root / "tests" / "test_main.py").exists()
    assert any((root / "test_outputs").iterdir())
    print("e2e ②：QA 写测试→沙箱真跑 pytest→通过→<self> 自环 ✅")

asyncio.run(main())
