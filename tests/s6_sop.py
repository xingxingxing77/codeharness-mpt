"""S6 门禁（随批增长）。本文件当前钉批 1（prompts 全量逐字复制）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s6_sop.py

口径（docs/施工3 批 1 原话）：**这是全项目唯一"不许优化"的地方**——所以断言不能打在
"文件文本 diff"上（import 前缀本来必须重写），要打在**每个模块级字符串常量**上：
解析出「顶层赋值里所有 str 字面量」逐个与源比对，一个字的措辞漂移都会红。
源仓 E:/MetaGPT 缺失时跳过（供体不是本仓的 CI 依赖），但本机在就必须跑。
"""
import ast
import asyncio
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO.parent / "MetaGPT" / "metagpt" / "prompts"
DST = REPO / "codeharness" / "prompts"

PROMPT_FILES = [  # 施工3 批1 的 16 件（路径相对 prompts/）
    "__init__.py", "product_manager.py", "summarize.py", "task_type.py", "sales.py",
    "tutorial_assistant.py", "invoice_ocr.py", "metagpt_sample.py", "generate_skill.md",
    "di/__init__.py", "di/role_zero.py", "di/swe_agent.py", "di/architect.py",
    "di/engineer2.py", "di/data_analyst.py", "di/team_leader.py", "di/write_analysis_code.py",
]


def module_strings(path: Path) -> dict:
    """顶层赋值里的字符串常量（含 f-string 的非插值段不参与：prompt 件里没有插值赋值）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for t in targets:
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    out[t.id] = node.value.value
    return out


def t1_prompts_verbatim():
    if not SRC.exists():
        print("  t1 跳过（供体 E:/MetaGPT/prompts 不在本机）")
        return
    checked = 0
    for rel in PROMPT_FILES:
        s, d = SRC / rel, DST / rel
        assert d.exists(), f"{rel} 没复制过来"
        if rel.endswith(".md"):
            assert d.read_text(encoding="utf-8") == s.read_text(encoding="utf-8"), f"{rel} 资产漂移"
            checked += 1
            continue
        src_s, dst_s = module_strings(s), module_strings(d)
        assert src_s.keys() == dst_s.keys(), \
            f"{rel} 常量集不等，缺 {set(src_s) - set(dst_s)} 多 {set(dst_s) - set(src_s)}"
        for k in src_s:
            assert src_s[k] == dst_s[k], f"{rel}:{k} 逐字性被破坏（源与仓内常量不相等）"
        checked += len(src_s)
    assert checked > 40, f"覆盖异常：只数到 {checked} 个常量，复制面缩水了？"
    print(f"  t1 {len(PROMPT_FILES)} 件 prompt 顶层字符串常量与源逐字相等（比中 {checked} 项）")


def t2_prompt_imports_and_consumers():
    """16 件 + 两个补齐的依赖件全部可 import；`strategy/task_type.py` 的枚举成员名与源一致。"""
    mods = ["codeharness.prompts", "codeharness.prompts.di.role_zero", "codeharness.prompts.di.swe_agent",
            "codeharness.prompts.di.architect", "codeharness.prompts.di.engineer2",
            "codeharness.prompts.di.data_analyst", "codeharness.prompts.di.team_leader",
            "codeharness.prompts.di.write_analysis_code", "codeharness.prompts.product_manager",
            "codeharness.prompts.summarize", "codeharness.prompts.task_type", "codeharness.prompts.sales",
            "codeharness.prompts.tutorial_assistant", "codeharness.prompts.invoice_ocr",
            "codeharness.prompts.metagpt_sample", "codeharness.strategy.task_type",
            "codeharness.tools.libs.data_preprocess"]
    for m in mods:
        importlib.import_module(m)
    from codeharness.strategy.task_type import TaskType
    from codeharness.tools.libs.data_preprocess import get_column_info
    import pandas as pd
    info = get_column_info(pd.DataFrame({"c": ["a", "b"], "n": [1, 2],
                                         "d": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                                         "o": [object(), None]}, dtype=object))
    assert set(info) == {"Category", "Numeric", "Datetime", "Others"}, info
    assert len(list(TaskType)) >= 5 and all(
        t.value.desc and isinstance(t.value.guidance, str) for t in TaskType)   # 枚举成员挂 prompt 资产
    print(f"  t2 {len(mods)} 件导入冒烟 + get_column_info 真跑 + TaskType 枚举可用")


PRD_JSON = ('{"language":"zh","programming_language":"python","original_requirements":"tinycli",'
            '"project_name":"p","product_goals":["g"],"user_stories":["u"],"competitive_analysis":["a"],'
            '"competitive_quadrant_chart":"quadrantChart\\n  title t","requirement_analysis":"ra",'
            '"requirement_pool":[["P0","core"]],"ui_design_draft":"s","anything_unclear":""}')


def _run(action, msg):
    return asyncio.run(action.run(msg))


def t3_write_prd_three_branches():
    """源 docstring 的三情形各一 fixture（门禁第 1 条：每 Action 一个 FakeLLM 剧本）。
    断言打在**分支行为**上：bugfix 不再发第二次模型调用；增量先问相关性、prompt 里必须
    带旧 PRD 正文（NEW_REQ_TEMPLATE 的 Legacy Content 段）；新建落三件产物。"""
    import shutil
    import codeharness.runtime as rt
    from codeharness.actions.write_prd import WritePRD
    from codeharness.const import BUGFIX_FILENAME, DocName, RepoName, RequirementTag
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message

    def fresh(tag):
        CURRENT_PROJECT.set(tag)
        store = ArtifactStore.active()
        shutil.rmtree(store.root, ignore_errors=True)
        return store

    try:
        store = fresh("s6prd_new")
        llm = FakeLLM([PRD_JSON])
        out = _run(WritePRD(llm=llm), Message(content="做个 tinycli"))
        assert (store.root / RepoName.PRD / DocName.PRD).exists()
        assert (store.root / RepoName.PRD / DocName.PRD_MD).exists()
        assert (store.root / RepoName.RESOURCES / "competitive_analysis.mmd").exists(), "方案C的.mmd没落盘"
        assert out.cause_by == "WritePRD" and set(out.instruct_content) >= {"project_name"}
        assert "PRD is completed" in out.content

        store = fresh("s6prd_upd")
        asyncio.run(store.save(RepoName.PRD, Document(filename=DocName.PRD,
                                                      content=PRD_JSON.replace("tinycli", "老版本"))))
        llm = FakeLLM(['{"is_relative":"YES","reason":"同一产品"}', PRD_JSON])
        out = _run(WritePRD(llm=llm), Message(content="加个 web UI"))
        assert len(llm.calls) == 2, f"相关性判定 + REFINED 两次调用: {len(llm.calls)}"
        asked = str(llm.calls[0])
        assert "Legacy Content" in asked and "加个 web UI" in asked, "NEW_REQ_TEMPLATE 没逐字进判定 prompt"

        store = fresh("s6prd_bug")
        (store.root / RepoName.SRC).mkdir(parents=True, exist_ok=True)
        (store.root / RepoName.SRC / "main.py").write_text("x=1\n", encoding="utf-8")
        llm = FakeLLM(['{"issue_type":"BUG","reason":"崩溃"}', PRD_JSON])
        out = _run(WritePRD(llm=llm), Message(content="程序启动就崩"))
        assert out.cause_by == RequirementTag.FIX_BUG, out.cause_by
        assert (store.root / RepoName.DOCS / BUGFIX_FILENAME).exists()
        assert len(llm.calls) == 1, "bugfix 分支不该再生成 PRD"
    finally:
        for d in ("s6prd_new", "s6prd_upd", "s6prd_bug"):
            shutil.rmtree(rt.session_root(d), ignore_errors=True)
        rt.CURRENT_PROJECT.set("")
    print("  t3 WritePRD 三分支（新建落三产物/增量带 Legacy Content 两段调用/bugfix 单调用转 FixBug）")


def t4_action_templates_verbatim():
    """加厚件里的模板常量与源逐字比对（批2 第 2 条：prompt 常量段保留源文本）。"""
    src = Path("E:/MetaGPT/metagpt/actions/write_prd.py")
    if not src.exists():
        print("  t4 跳过（供体不在）")
        return
    s_consts = module_strings(src)
    d_consts = module_strings(REPO / "codeharness" / "actions" / "write_prd.py")
    for k in ("CONTEXT_TEMPLATE", "NEW_REQ_TEMPLATE"):
        assert k in s_consts and s_consts[k] == d_consts.get(k), f"{k} 与源不逐字"
    print("  t4 CONTEXT_TEMPLATE / NEW_REQ_TEMPLATE 与源逐字相等")


DESIGN_JSON = ('{"implementation_approach":"单文件 argparse","file_list":["main.py"],'
               '"data_structures_and_interfaces":"classDiagram\\nclass Cli","program_call_flow":'
               '"sequenceDiagram\\nCli->>Cli: run()","anything_unclear":"无"}')
TASKS_JSON = ('{"task_list":[{"filename":"main.py","task_id":"task_001","dependent_task_ids":[],'
              '"instruction":"实现 argparse"}],"required_packages":["rich","pydantic"],'
              '"shared_knowledge":"入口叫 main.py"}')


def _fixture_store(tag: str):
    """一个场景一个会话目录：先删后建，收尾统一清（s6 不留下磁盘垃圾，同 s3b t9 纪律）。"""
    import shutil
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.runtime import CURRENT_PROJECT
    CURRENT_PROJECT.set(tag)
    store = ArtifactStore.active()
    shutil.rmtree(store.root, ignore_errors=True)
    return store


def t5_write_design_branches():
    """新建 vs REFINED 增量：prompt 必须分别带上五字段原文与 Legacy Content；
    产物=design.json（真源）+design.md（人读）+两图 .mmd（方案 C 落盘）。"""
    import shutil
    import asyncio as A
    from codeharness.actions.design_api import WriteDesign
    from codeharness.actions.write_prd import WritePRD
    from codeharness.const import DocName, RepoName
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message
    try:
        store = _fixture_store("s6des_new")
        A.run(store.save(RepoName.PRD, Document(filename=DocName.PRD, content=PRD_JSON)))
        llm = FakeLLM([DESIGN_JSON])
        out = A.run(WriteDesign(llm=llm).run(Message(content="设计一下")))
        assert (store.root / RepoName.DOCS / DocName.DESIGN_JSON).exists()
        assert (store.root / RepoName.DOCS / DocName.DESIGN).exists()
        assert (store.root / RepoName.RESOURCES / "data_api_design.mmd").exists()
        assert (store.root / RepoName.RESOURCES / "seq_flow.mmd").exists(), "方案C 的 .mmd 没落盘"
        asked = str(llm.calls[0])
        assert "classDiagram" in asked and "Designing is complete" in out.content
        assert set(out.instruct_content) == {"implementation_approach", "file_list",
                                             "data_structures_and_interfaces", "program_call_flow",
                                             "anything_unclear"}, out.instruct_content.keys()
        llm2 = FakeLLM([DESIGN_JSON])
        A.run(WriteDesign(llm=llm2).run(Message(content="改设计了")))     # 旧 design.json 在 → REFINED
        asked2 = str(llm2.calls[0])
        assert "Legacy Content" in asked2 and "incremental development" in asked2, "增量分支没走 REFINED 原文"
    finally:
        shutil.rmtree(_ws("s6des_new"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t5 WriteDesign：新建落 json/md/两 .mmd、字段集与源 NODES 相等、增量走 Legacy+REFINED 原文")


def t6_write_tasks_requirements():
    """任务表 + required_packages 聚合出 requirements.txt（源 _update_requirements :154 的落地面）。"""
    import shutil
    import asyncio as A
    from codeharness.actions.project_management import WriteTasks
    from codeharness.const import DocName, PACKAGE_REQUIREMENTS_FILENAME, RepoName
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message
    try:
        store = _fixture_store("s6tasks")
        A.run(store.save(RepoName.DOCS, Document(filename=DocName.DESIGN_JSON, content=DESIGN_JSON)))
        llm = FakeLLM([TASKS_JSON])
        out = A.run(WriteTasks(llm=llm).run(Message(content="拆任务")))
        assert (store.root / RepoName.DOCS / DocName.TASKS).exists()
        req = (store.root / PACKAGE_REQUIREMENTS_FILENAME).read_text(encoding="utf-8").splitlines()
        assert req == ["pydantic", "rich"], req                     # 聚合 + 去重 + 稳定序
        asked = str(llm.calls[0])
        assert "argparse" in asked, "任务表 prompt 没读 design.json 的内容"
        assert out.cause_by == "WriteTasks" and "WBS is completed" in out.content
        llm2 = FakeLLM([TASKS_JSON])                                 # 旧 tasks 在 → REFINED 增量
        A.run(WriteTasks(llm=llm2).run(Message(content="再拆")))
        assert "Legacy Content" in str(llm2.calls[0])
    finally:
        shutil.rmtree(_ws("s6tasks"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t6 WriteTasks：requirements.txt 聚合去重、design.json 做底、增量带 Legacy Content")


def t7_run_code_summary():
    """源 run_code 的 LLM 复盘段（此前整段缺失）：跑完必须问一次模型，summary 进存档 JSON。"""
    import shutil
    import asyncio as A
    from codeharness.actions.run_code import RunCode
    from codeharness.const import RepoName
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Message, RunCodeContext
    try:
        store = _fixture_store("s6run")
        (store.root / RepoName.TESTS).mkdir(parents=True, exist_ok=True)
        (store.root / RepoName.TESTS / "test_fail.py").write_text("def t():\n    assert False\n", encoding="utf-8")
        ctx = RunCodeContext(test_filename="test_fail.py")
        out = A.run(RunCode(llm=FakeLLM(["## instruction:\n断言没写对\n## File To Rewrite:\ntest_fail.py\n"
                                         "## Status:\nFAIL\n## Send To:\nQaEngineer\n"])).run(
            Message(content="跑", instruct_content=ctx.model_dump(), instruct_schema="RunCodeContext")))
        assert "测试失败" in out.content
        import json as _j
        saved = store.root / RepoName.TEST_OUTPUTS / "output_test_fail.py.json"
        assert saved.exists() and "File To Rewrite" in _j.loads(saved.read_text(encoding="utf-8"))["summary"], \
            "RunCodeResult.summary 没进存档"
        assert "QaEngineer" in out.content, "复盘摘要没回给路由可见的消息"
    finally:
        shutil.rmtree(_ws("s6run"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t7 RunCode：沙箱真跑 pytest + 复盘段进存档与消息（ok 仍按 return_code，不复活死正则）")


def _ws(tag):
    from codeharness.configs.settings import settings
    return Path(settings.workspace_root) / tag


def main():
    checks = [t1_prompts_verbatim, t2_prompt_imports_and_consumers,
              t3_write_prd_three_branches, t4_action_templates_verbatim,
              t5_write_design_branches, t6_write_tasks_requirements, t7_run_code_summary]
    for c in checks:
        c()
    print(f"\nS6 门禁通过：{len(checks)} 组 —— 批1 prompt 逐字 2 组 + 批2a 加厚 fixture/模板逐字 5 组"
          f"；后续批在此续加（build_role 全名 / N2 外部注册演示 / 2b-2f 各 Action 一 fixture）")


if __name__ == "__main__":
    sys.exit(main())
