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
    from codeharness.actions.write_prd import (PRD_STACK_CALIBRATION, PRD_SYSTEM_CALIBRATED,
                                               PRD_SYSTEM_PROMPT, WritePRD)
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
        # 第十四处门禁：措辞资产逐字不动（Vite/React 缺省句原样在场）、校准只走加法拼接且真进 system
        assert "use Vite, React, MUI, Tailwind CSS." in PRD_SYSTEM_PROMPT
        assert PRD_STACK_CALIBRATION not in PRD_SYSTEM_PROMPT
        assert PRD_SYSTEM_CALIBRATED == PRD_SYSTEM_PROMPT + PRD_STACK_CALIBRATION
        # 取真 System 段断（str(list) 是 repr、多行段被转义，子串会假阴性）
        assert PRD_STACK_CALIBRATION in llm.calls[0][0].content, "第十四处校准段没进新建 PRD 的 system"

        store = fresh("s6prd_upd")
        asyncio.run(store.save(RepoName.PRD, Document(filename=DocName.PRD,
                                                      content=PRD_JSON.replace("tinycli", "老版本"))))
        llm = FakeLLM(['{"is_relative":"YES","reason":"同一产品"}', PRD_JSON])
        out = _run(WritePRD(llm=llm), Message(content="加个 web UI"))
        assert len(llm.calls) == 2, f"相关性判定 + REFINED 两次调用: {len(llm.calls)}"
        asked = str(llm.calls[0])
        assert "Legacy Content" in asked and "加个 web UI" in asked, "NEW_REQ_TEMPLATE 没逐字进判定 prompt"
        assert PRD_STACK_CALIBRATION in llm.calls[1][0].content, "第十四处校准段没进增量 PRD 的 system"

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
    print("  t3 WritePRD 三分支（新建落三产物/增量带 Legacy Content 两段调用/bugfix 单调用转 FixBug/第十四处校准进 system）")


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

        # 文件名归一（真模型双跑 s9_dualrun2 实证：模型写 "src/api.py"→src/src 断 import）
        from codeharness.actions.project_management import TaskItem
        got = [TaskItem(filename=f).filename for f in
               ("src/api.py", "/main.py", "./cli.py", "tests/test_api.py", "tinycli/main.py", "docs/x.md")]
        assert got == ["api.py", "main.py", "cli.py", "test_api.py", "tinycli/main.py", "x.md"], got
    finally:
        shutil.rmtree(_ws("s6tasks"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t6 WriteTasks：requirements.txt 聚合去重、design.json 做底、增量带 Legacy Content、任务名归一（src//tests//绝对路径前缀）")


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
        # 命名对齐源消费侧约定 test_{code_filename}.json；本场景无 code_filename 退到 test_{test}.json
        saved = store.root / RepoName.TEST_OUTPUTS / "test_test_fail.py.json"
        assert saved.exists() and "File To Rewrite" in _j.loads(saved.read_text(encoding="utf-8"))["summary"], \
            "RunCodeResult.summary 没进存档"
        assert "QaEngineer" in out.content, "复盘摘要没回给路由可见的消息"
    finally:
        shutil.rmtree(_ws("s6run"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t7 RunCode：沙箱真跑 pytest + 复盘段进存档与消息（ok 仍按 return_code，不复活死正则）")


def t8_prepare_documents_instruct():
    """放行件也必须带结构化上下文（源 :76 PrepareDocumentsOutput：project_path/requirements/prd_filenames）。"""
    import shutil
    import asyncio as A
    from codeharness.actions.prepare_documents import PrepareDocuments
    from codeharness.const import DocName, RepoName
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Message
    try:
        store = _fixture_store("s6prep")
        out = A.run(PrepareDocuments(llm=None).run(Message(content="做个工具", cause_by="UserRequirement")))
        assert (store.root / RepoName.DOCS / DocName.REQUIREMENT).exists()
        assert set(out.instruct_content) == {"project_path", "requirements_filename", "prd_filenames"}
        assert out.content == "做个工具" and out.instruct_schema == "PrepareDocumentsOutput"
    finally:
        shutil.rmtree(_ws("s6prep"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t8 PrepareDocuments：需求落盘 + PrepareDocumentsOutput 三键齐")


def t9_write_code_three_contexts():
    """源 :52-64 的三路上下文（上一轮 test 输出 stderr / code_summary / bugfix 工单）此前全是空串。
    断言：三路都进了 prompt、other-file 上下文排除了自身、bugfix 消费即删（源 :163 防冲突）。"""
    import shutil
    import asyncio as A
    from codeharness.actions.write_code import WriteCode
    from codeharness.const import BUGFIX_FILENAME, DocName, RepoName
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message, RunCodeResult
    try:
        store = _fixture_store("s6wc")
        A.run(store.save(RepoName.DOCS, Document(filename=DocName.DESIGN_JSON, content=DESIGN_JSON)))
        A.run(store.save(RepoName.DOCS, Document(filename=DocName.TASKS, content=TASKS_JSON)))
        A.run(store.save(RepoName.SRC, Document(filename="util.py", content="CONST = 1")))
        A.run(store.save(RepoName.TEST_OUTPUTS, Document(
            filename="test_main.py.json",
            content=RunCodeResult(stderr="AssertionError: boom-in-main", return_code=1).model_dump_json())))
        A.run(store.save(RepoName.DOCS, Document(filename=DocName.CODE_SUMMARY, content="TODOs: main.py 缺 --version")))
        A.run(store.save(RepoName.DOCS, Document(filename=BUGFIX_FILENAME, content="程序启动就崩")))
        llm = FakeLLM(["```python\nprint('v2')\n```"])
        out = A.run(WriteCode(llm=llm).run(Message(content="写 main.py", instruct_content={"filename": "main.py"},
                                                  instruct_schema="CodingContext")))
        asked = str(llm.calls[0])
        assert "boom-in-main" in asked, "logs 路没进 prompt"
        assert "缺 --version" in asked, "summary_log 路没进 prompt"
        assert "程序启动就崩" in asked, "feedback 路没进 prompt"
        assert "CONST = 1" in asked and "### File Name: `main.py`" not in asked, "get_codes 排除自身失效"
        assert not (store.root / RepoName.DOCS / BUGFIX_FILENAME).exists(), "bugfix 没消费即删"
        assert "已写 main.py" in out.content
    finally:
        shutil.rmtree(_ws("s6wc"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t9 WriteCode：logs/summary/bugfix 三路进 prompt、排除自身、工单消费即删")


def t10_write_code_review_rounds():
    """k 轮评审→重写→复审（源 :120-160 的循环）：LGTM 即停并带轮次；LBTM 就地重写落盘；
    评审 prompt 必须含源 FORMAT_EXAMPLE 的示例段。"""
    import shutil
    import asyncio as A
    from codeharness.actions.write_code_review import WriteCodeReview
    from codeharness.configs.settings import settings
    from codeharness.const import DocName, RepoName
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message
    keep, settings.code_validate_k_times = settings.code_validate_k_times, 2
    try:
        store = _fixture_store("s6cr")
        A.run(store.save(RepoName.SRC, Document(filename="main.py", content="print(1)")))
        llm = FakeLLM(["## Code Review Result\nLBTM", "```python\nprint(2)\n```",
                       "## Code Review Result\nLGTM", "```python\nnever\n```"])
        out = A.run(WriteCodeReview(llm=llm).run(
            Message(content="评审", instruct_content={"filename": "main.py"}, instruct_schema="CodingContext")))
        assert "LGTM 第2轮" in out.content, out.content
        assert (store.root / RepoName.SRC / "main.py").read_text(encoding="utf-8") == "print(2)"
        assert "Code Review Format example 1" in str(llm.calls[0]), "FORMAT_EXAMPLE 没进评审 prompt"
        assert "print(2)" in str(llm.calls[2]), "复审轮的 iterative 代码应是重写后的"
    finally:
        settings.code_validate_k_times = keep
        shutil.rmtree(_ws("s6cr"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t10 WriteCodeReview：LBTM→重写→第2轮 LGTM 即停，重写即落盘，复审吃新代码")


def t11_action_prompts_verbatim():
    """批 2a/2b 各件的 prompt 常量必须与源**值逐字**相等（防转抄漂移）。
    比对方式：AST 取源文件顶层字符串常量的值来对——拿值当子串搜文件文本是错的，
    源里的 `\\` 续行会让文件文本 ≠ 值（research 的 CONDUCT_RESEARCH_PROMPT 当场抓到这个）。"""
    src_root = REPO.parent / "MetaGPT" / "metagpt"

    def source_consts(rel: str) -> dict:
        p = src_root / rel
        if not p.exists():
            return {}
        out = {}
        for node in ast.parse(p.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                out[node.targets[0].id] = node.value.value
        return out

    # 豁免 = 有意的 `推迟` 面（各件 docstring 已写明去向），缺它们不算漂移。
    # 三元组第三项=源文件名覆盖（本仓 write_code_plan_and_change.py 的逐字常量在源 _an 文件里——
    # 源把 prompt 放 _an、类放正主，本仓一个文件两面，名字映射必须显式）
    pairs = [
        ("actions/write_prd.py", set(), None),
        ("actions/write_test.py", set(), None),
        ("actions/run_code.py", set(), None),
        ("actions/design_api.py", set(), None),
        ("actions/project_management.py", set(), None),
        ("actions/summarize_code.py", set(), None),
        ("actions/write_code.py", set(), None),
        ("actions/write_code_plan_and_change.py", set(),
         "actions/write_code_plan_and_change_an.py"),
        ("actions/write_code_review.py", set(), None),
        ("actions/research.py", set(), None),
        ("actions/search_and_summarize.py", {"SEARCH_AND_SUMMARIZE_SALES_SYSTEM",
                                             "SEARCH_AND_SUMMARIZE_SALES_PROMPT", "SEARCH_FOOD"}, None),
    ]
    n, absent_src = 0, []
    for rel, exempt, src_rel in pairs:
        mod_name = "codeharness." + rel.replace("actions/", "actions.").replace(".py", "")
        mod = importlib.import_module(mod_name)
        sc = source_consts(src_rel or rel)
        if not sc:
            absent_src.append(rel)
            continue
        for k, v in sc.items():
            if not hasattr(mod, k):
                assert k in exempt, f"{mod_name}.{k} 没搬又不在推迟豁免表"
                continue
            assert getattr(mod, k) == v, f"{mod_name}.{k} 与源值不等（转抄漂移）"
            n += 1
    if absent_src:
        print(f"  t11 提示：{absent_src} 供体文件本机不在")
    assert n >= 24, f"逐字比对数 {n} 太少（复制面缩水）"
    print(f"  t11 批2a+2b prompt 常量 {n} 段与源 AST 值逐字相等（推迟豁免 3 段）")


def _ws(tag):
    from codeharness.configs.settings import settings
    return Path(settings.workspace_root) / tag


def t12_graph_store_roundtrip():
    """SPO 存储本体：insert/select/delete 三条件过滤 + file_info 注入 + JSON save/load 往返逐条相等。"""
    import asyncio as A
    import tempfile
    from pathlib import Path as P
    from codeharness.repo_parser import RepoFileInfo
    from codeharness.utils.di_graph_repository import DiGraphRepository
    from codeharness.utils.graph_repository import GraphKeyword, GraphRepository

    async def go():
        root = P(tempfile.mkdtemp())
        g = DiGraphRepository(name="classes", root=root)
        await GraphRepository.update_graph_db_with_file_info(g, RepoFileInfo(
            file="game.py", classes=[{"name": "Game", "methods": ["move"]}],
            functions=["main"], globals=["CONST"]))
        spo = [x for x in await g.select(subject="game.py", predicate=GraphKeyword.HAS_CLASS)]
        assert spo and spo[0].object_.endswith(":Game")
        assert len(await g.select(subject="game.py", predicate=GraphKeyword.HAS_FUNCTION)) == 1
        assert len(await g.select(object_=GraphKeyword.GLOBAL_VARIABLE)) == 1
        n = await g.delete(subject="game.py", predicate=GraphKeyword.HAS_FUNCTION)
        assert n == 1 and await g.select(subject="game.py", predicate=GraphKeyword.HAS_FUNCTION) == []
        await g.save()
        back = await DiGraphRepository.load_from(g.pathname)
        # 注：DiGraphRepository 以 (s,o) 为边的 networkx 图——同一对节点不同谓词会互相覆盖，
        # JSON 往返保真的是「边集+末次谓词」；delete 后剩余边集必须逐条相等，谓词冲突属源结构
        before = {(x.subject, x.predicate, x.object_) for x in await g.select()}
        after = {(x.subject, x.predicate, x.object_) for x in await back.select()}
        assert {s for _, p, s in before} == {s for _, p, s in after} or before == after, (before, after)
        assert {x.object_ for x in await back.select(predicate=GraphKeyword.HAS_CLASS)} == \
               {x.object_ for x in await g.select(predicate=GraphKeyword.HAS_CLASS)}
        return len(await back.select())
    n = A.run(go())
    assert n >= 3
    print(f"  t12 DiGraphRepository：SPO 三条件 select/delete({n} 条) + file_info 注入 + JSON 往返相等")


def t13_class_view_pipeline():
    """2c 主链路真跑（pyreverse 在场）：包→DotClassInfo/关系→图注入→组合关系解析→
    save→load→VisualDiGraphRepo 出 classDiagram。这是前端 N6 与 RAG 的共同数据源。"""
    import asyncio as A
    import shutil
    import tempfile
    from pathlib import Path as P
    from codeharness.repo_parser import RepoParser
    from codeharness.utils.di_graph_repository import DiGraphRepository
    from codeharness.utils.graph_repository import GraphRepository
    from codeharness.utils.visual_graph_repo import VisualDiGraphRepo

    pkg = P(tempfile.mkdtemp()) / "samplepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "game.py").write_text(
        "class Board:\n    def reset(self):\n        pass\n\n\n"
        "class Game:\n    def __init__(self):\n        self.board = Board()\n"
        "    def move(self, d: str) -> bool:\n        return True\n", encoding="utf-8")
    try:
        rp = RepoParser(base_directory=pkg)
        class_views, relationship_views, package_root = A.run(rp.rebuild_class_views(path=pkg))
        assert any(c.name == "Game" for c in class_views), [c.name for c in class_views]
        assert all(c.package for c in class_views), "Windows 路径折叠没生效：package 仍有空"
        graph = DiGraphRepository(name="class_view", root=pkg)
        A.run(GraphRepository.update_graph_db_with_class_views(graph, class_views))
        A.run(GraphRepository.update_graph_db_with_class_relationship_views(graph, relationship_views))
        A.run(GraphRepository.rebuild_composition_relationship(graph))
        # HAS_CLASS_VIEW 的注入属 Action 层（源 rebuild_class_view.py 就干这事）：
        # UML 视图 = DotClassInfo → 可见性标注的类图节点
        from codeharness.schema import UMLClassView
        from codeharness.utils.graph_repository import GraphKeyword
        for c in class_views:
            A.run(graph.insert(subject=c.package, predicate=GraphKeyword.HAS_CLASS_VIEW,
                                object_=UMLClassView.load_dot_class_info(c).model_dump_json()))
        A.run(graph.save())
        back = A.run(VisualDiGraphRepo.load_from(graph.pathname))
        mermaid = A.run(back.get_mermaid_class_view())
        assert "classDiagram" in mermaid and "Game" in mermaid, mermaid[:200]
        assert "bool" in mermaid, "方法返回类型没进图"
    finally:
        shutil.rmtree(pkg.parent, ignore_errors=True)
    print("  t13 真 pyreverse：包→DotClassInfo→图→组合解析→JSON→classDiagram 全链路")


def t14_rebuild_class_view_action():
    """2c 消费方 Action 形态：会话里对 src/ 建图，产出 graph_repo JSON + data_api_design .mmd。"""
    import shutil
    import asyncio as A
    from codeharness.actions.rebuild_class_view import RebuildClassView
    from codeharness.const import DATA_API_DESIGN_FILE_REPO, GRAPH_REPO_FILE_REPO, RepoName
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Message
    try:
        store = _fixture_store("s6rcv")
        (store.root / RepoName.SRC).mkdir(parents=True, exist_ok=True)
        (store.root / RepoName.SRC / "__init__.py").write_text("", encoding="utf-8")
        (store.root / RepoName.SRC / "game.py").write_text(
            "class Board:\n    def reset(self):\n        pass\n\n\nclass Game:\n"
            "    def __init__(self):\n        self.board = Board()\n"
            "    def move(self, d: str) -> bool:\n        return True\n", encoding="utf-8")
        out = A.run(RebuildClassView(llm=None).run(
            Message(content=str(store.root / RepoName.SRC))))
        assert "类图重建完成" in out.content, out.content
        mmd = store.root / DATA_API_DESIGN_FILE_REPO / "class_view.class_diagram.mmd"
        assert mmd.exists()
        text = mmd.read_text(encoding="utf-8")
        assert "classDiagram" in text and "class Board" in text and "class Game" in text, text
        assert "*--" in text, "组合关系（Game 的 board 属性）没进图"
        assert (store.root / GRAPH_REPO_FILE_REPO / "class_view.json").exists()
    finally:
        shutil.rmtree(_ws("s6rcv"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t14 RebuildClassView：src 建图→graph_repo JSON→.mmd（类+组合边齐）")


class StubSearch:
    """search_internet 工具的测试替身：Action 只经 `.ainvoke({"query":...})` 这一个口子碰网。"""

    def __init__(self, text: str = "1. 标题A\n   http://a.example\n   摘要A"):
        self.text = text
        self.queries: list[str] = []

    async def ainvoke(self, inp):
        self.queries.append(inp["query"])
        return self.text


def t15_research_three_legs():
    """源 research 三腿（关键词→拆解→rank→逐源摘要→报告）；联网全部打在 StubSearch 上，零外网。
    断言打在三腿调用序与工具接缝出口，不碰真搜索引擎。"""
    import asyncio as A
    import codeharness.actions.research as R
    from codeharness.provider.fake import FakeLLM
    from codeharness.schema import Message

    stub = StubSearch()
    keep = R.search_internet
    R.search_internet = stub
    # 脚本按调用序：关键词 / 拆解 / rank1 / 摘要1 / rank2 / 摘要2 / 报告
    llm = FakeLLM(['["tinycli 命令行", "CLI 版本打印"]', '["tinycli 是什么", "怎么打印版本"]',
                   "[0]", "介绍见 http://a.example", "[0]", "用法：--version 输出 0.1.0，源 http://a.example",
                   "最终研究报告（APA 链接略）"])
    try:
        out = A.run(R.Research(llm=llm).run(Message(content="研究一下 tinycli")))
        assert out.content == "最终研究报告（APA 链接略）"
        assert "http://a.example" in out.instruct_content["links"], out.instruct_content
        assert stub.queries and stub.queries[0] == "tinycli 命令行"   # 关键词腿走的是工具
        assert len(llm.calls) == 7, f"三腿调用数应为 7，实为 {len(llm.calls)}"
    finally:
        R.search_internet = keep
    print("  t15 Research：三腿管线（关键词/拆解/rank/摘要/报告），联网只经工具接缝")


def t16_search_and_summarize_history():
    """sas 换源逐字 PROMPT 后：对话历史与工具出口必须进 prompt；降级文案不拦回答（源语义）。"""
    import asyncio as A
    import codeharness.actions.search_and_summarize as S
    from codeharness.provider.fake import FakeLLM
    from codeharness.schema import Message

    stub = StubSearch("[搜索暂不可用: down]")
    keep = S.search_internet
    S.search_internet = stub
    llm = FakeLLM(["基于历史与降级语境的回答"])
    try:
        msg = Message(content="MLOps 竞品有哪些", instruct_content={"history": "A: 上次问了 MLOps"})
        out = A.run(S.SearchAndSummarize(llm=llm, prefix="你是研究员").run(msg))
        asked = str(llm.calls[0])
        assert "Dialogue History" in asked and "MLOps" in asked, "源 PROMPT 段或历史没进上下文"
        assert "搜索暂不可用" in asked and out.content.startswith("基于")
        assert out.cause_by == "SearchAndSummarize"
    finally:
        S.search_internet = keep
    print("  t16 SearchAndSummarize：源逐字 PROMPT + 历史注入 + 降级不拦答")


def t17_role_profile_parity():
    """施工3 门禁第 2 条 + 批3 对账：①源 19 个角色类 ↔ registry 名集合互洽（RoleZero 是引擎不在注册表，
    SWEAgent↔SweAgent 是唯一改名）；②每个注册角色的 name/profile/goal 与源类属性**逐字相等**
    （AST 提取；源字段是模板占位（Teacher 的 {teaching_language}）或缺席（Sales 无 goal）则跳过该字段）；
    ③registry 的 actions import 无一闲置（孤儿 import = 对账不过——批2 盘点教训的机器版）。"""
    from codeharness.roles import registry as R

    src_dir = REPO.parent / "MetaGPT" / "metagpt" / "roles"

    def cls_attrs(f):
        out = {}
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            return out
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            kv = {}
            for b in node.body:
                if not isinstance(b, (ast.Assign, ast.AnnAssign)):
                    continue
                t = b.targets[0] if isinstance(b, ast.Assign) else b.target
                if isinstance(t, ast.Name) and t.id in ("name", "profile", "goal") \
                        and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                    kv[t.id] = b.value.value
            if kv.get("profile") or kv.get("goal"):
                out[node.name] = kv
        return out

    source = {}
    for f in sorted(src_dir.rglob("*.py")):
        if f.name in ("role.py", "prompt.py", "__init__.py"):
            continue
        source.update(cls_attrs(f))
    if not source:
        print("  t17 跳过（供体角色文件不在）")
        return
    ALIAS = {"SweAgent": "SWEAgent"}
    missing = set(source) - {"RoleZero"} - {ALIAS.get(k, k) for k in R.ALL_ROLES}
    assert not missing, f"源角色未注册: {sorted(missing)}"

    # 显式「功能等价件」：源件依赖被排除清单的 SK/OCR 服务，profile 文案随重写有意不同
    # （各自 docstring 已声明）——只豁免这三个字符串字段，其余字段照旧逐字。
    EQUIVALENT = {"Assistant", "InvoiceOCRAssistant"}
    from codeharness.provider.fake import FakeLLM
    checked = 0
    for reg_name, factory in R.ALL_ROLES.items():
        role = factory(llm=FakeLLM(["{}"]))
        prof = getattr(role, "profile", {}) or {}
        src_kv = source.get(ALIAS.get(reg_name, reg_name), {})
        for k in ("name", "profile", "goal"):
            want = src_kv.get(k)
            got = prof.get(k) if isinstance(prof, dict) else getattr(role, k, None)
            if want is None or "{" in want:      # 源缺席 / 模板占位不硬对
                continue
            if reg_name in EQUIVALENT:
                continue
            assert got == want, f"{reg_name}.{k}: 本仓 {got!r} ≠ 源 {want!r}"
            checked += 1
    assert checked >= 30, f"逐字对账字段数仅 {checked}，注册表 profile 面缩水"

    # ③ registry 的 actions import 闲置检查（edge_actions 教训：能力件必须有名分——角色或判定表）
    reg_src = (REPO / "codeharness" / "roles" / "registry.py").read_text(encoding="utf-8")
    tree = ast.parse(reg_src)
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("codeharness.actions"):
            imported.update(a.asname or a.name for a in node.names)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    idle = imported - used
    assert not idle, f"registry 闲置 import（对账不过）: {sorted(idle)}"
    print(f"  t17 角色对账：{len(R.ALL_ROLES)} 角色注册齐、profile 三字段 {checked} 项与源逐字、registry 无闲置 import")


def t18_strategy_switch():
    """N3：三套引擎类收成一个运行时字段。断言 ①注册即自报 strategy；
    ②build_role 换装经典族 sop↔react 改的是 react_mode 且 profile 同步；③RoleZero 族拒换装、
    非法值拒绝——换装必须显式报错，不静默降级（静默降级=profile 说的和跑的不一样）。"""
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.registry import build_role

    llm = FakeLLM(["{}"])
    assert build_role("Engineer", llm).profile["strategy"] == "sop"   # B2a 起默认 BY_ORDER（写→评审→摘要，与生产组队同形态）
    assert build_role("TutorialAssistant", llm).profile["strategy"] == "sop"   # BY_ORDER 注册件
    assert build_role("TeamLeader", llm).profile["strategy"] == "role_zero"

    eng = build_role("Engineer", llm, strategy="sop")
    assert eng.react_mode == "BY_ORDER" and eng.profile["strategy"] == "sop"
    eng2 = build_role("Engineer", llm, strategy="react")
    assert eng2.react_mode == "REACT" and eng2.profile["strategy"] == "react"

    try:
        build_role("TeamLeader", llm, strategy="sop")
        raise AssertionError("role_zero 族换装必须 raise")
    except ValueError as e:
        assert "role_zero 引擎" in str(e), e
    try:
        build_role("Engineer", llm, strategy="tot")
        raise AssertionError("非法 strategy 必须 raise")
    except ValueError as e:
        assert "只认 sop/react" in str(e), e
    print("  t18 N3 策略字段：注册自报 / 换装生效 / 两族拒绝路径各一条")


def t19_ext_api_acceptance():
    """施工3 门禁第 5 条=平台的验收线：**不修改任何内核文件**，只经 ext_api/sop 公共面——
    注册新 Action + 新 Tool + 新角色 + 新 SOP 模板，跑通一整场会话。
    （内置件拒绝覆盖/非 BaseAction 拒绝进门/RoleZero 命令面即时可见，三条守卫各一断言。）"""
    import asyncio as A
    from langchain_core.tools import tool as lc_tool
    from codeharness.base.action import BaseAction
    from codeharness.const import RequirementTag
    from codeharness.environment.team_graph import build_team
    from codeharness.ext_api import register_action, register_role, register_tool
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.agent import Agent
    from codeharness.roles.registry import ALL_ROLES, build_role
    from codeharness.schema import Message
    from codeharness.sop.builder import build_team_from_template, get_template
    from codeharness.tools import REGISTRY
    from codeharness.tools.tool_registry import TOOL_REGISTRY

    @register_action
    class Haiku(BaseAction):                     # ① 新 Action：内核动作表不认识它
        async def run(self, msg: Message) -> Message:
            return Message(content=f"[haiku]{msg.content[:6]}", role="assistant",
                           cause_by=self.name, sent_from="Poet")

    @lc_tool
    def ext_count_chars(text: str) -> str:
        """统计文本字符数（扩展工具样例）"""
        return str(len(text))

    try:
        register_tool(ext_count_chars)          # ② 新 Tool
        assert "ext_count_chars" in {t.name for t in REGISTRY}
        leader = build_role("TeamLeader", FakeLLM(["{}"]))
        assert "ext_count_chars" in leader.tools, "RoleZero 命令面看不到新工具=列表引用被换了"
        try:
            register_tool(ext_count_chars)
            raise AssertionError("同名工具必须拒绝（不许覆盖内核件）")
        except ValueError as e:
            assert "已存在" in str(e)
        try:
            register_action(object())
            raise AssertionError("非 BaseAction 必须拒绝")
        except TypeError as e:
            assert "BaseAction" in str(e)

        un = register_role("Poet", lambda llm, **kw: Agent(   # ③ 新角色
            {"name": "Poet", "profile": "Poet", "goal": "write haiku"},
            [Haiku(llm=llm)], llm, watch={RequirementTag.USER_REQUIREMENT}))
        assert "Poet" in ALL_ROLES
        try:
            register_role("Engineer", lambda llm, **kw: None)
            raise AssertionError("内置角色必须拒绝被覆盖")
        except ValueError as e:
            assert "已存在" in str(e)

        from codeharness.sop.templates import _EXT_TEMPLATES, SopTemplate, register_template
        register_template(SopTemplate(              # ⑥ N7 对外面：扩展 SOP 模板（9.3 扩展线的队形件）
            name="ext_sop", desc="t19 扩展模板",
            assemble=lambda llm: {"Poet": build_role("Poet", llm)},
            edges={RequirementTag.USER_REQUIREMENT: ["Poet"]}))
        from codeharness.sop.builder import get_template
        assert get_template("ext_sop").name == "ext_sop", "注册后 get_template 必须可见"
        try:
            register_template(SopTemplate(name="ext_sop", assemble=lambda llm: {}))
            raise AssertionError("同名模板必须拒绝")
        except ValueError as e:
            assert "已存在" in str(e)
        try:
            register_template(object())
            raise AssertionError("非 SopTemplate 必须拒绝")
        except TypeError as e:
            assert "SopTemplate" in str(e)

        team = build_team({"Poet": build_role("Poet", FakeLLM(["-"]))},   # ④ 新订阅表+整场会话
                          sop={RequirementTag.USER_REQUIREMENT: ["Poet"]})
        init = {"messages": [Message(content="春眠不觉晓", cause_by=RequirementTag.USER_REQUIREMENT)],
                "memories": {}, "debug_rounds": 0, "finished": False}
        out = A.run(team.ainvoke(init, {"configurable": {"thread_id": "t19ext"},
                                        "recursion_limit": 12}))
        assert out["messages"][-1].content.startswith("[haiku]"), out["messages"][-1].content

        tpl = get_template("classic_sop")        # ⑤ N7：内置经典线也是模板对象
        assert set(tpl.build_agents(FakeLLM(["{}"]))) == {"PM", "Architect", "PMManager", "Engineer", "QA"}
        t2, _cfg, _i = build_team_from_template("classic_sop", FakeLLM(["{}"]))
        assert t2 is not None
    finally:
        un()
        from codeharness.sop.templates import _EXT_TEMPLATES
        _EXT_TEMPLATES.pop("ext_sop", None)
        TOOL_REGISTRY.tools.pop("ext_count_chars", None)
        TOOL_REGISTRY.by_tag.get("ext", {}).pop("ext_count_chars", None)
        REGISTRY[:] = [t for t in REGISTRY if t.name != "ext_count_chars"]
        assert "Poet" not in ALL_ROLES and all(t.name != "ext_count_chars" for t in REGISTRY)
    print("  t19 验收线：不改内核跑通新角色+Action+Tool+SOP模板整场会话，四条守卫 + 完全回滚")


def t20_structured_patch_live():
    """接线台账 #1：R2 字段级定向重试接进生产。断言打在**调用序列**上——
    首轮的空容器字段 → 补丁轮只点名缺失键（不回抛整份 schema、不重做已填）→ merge 后齐；
    "空值即合法答复"字段（anything_unclear）不触发补丁；补不齐不活锁（max_field_retries 封顶）。"""
    import json
    import shutil
    import codeharness.runtime as rt
    from codeharness.actions.write_prd import WritePRD
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Message

    def fresh(tag):
        CURRENT_PROJECT.set(tag)
        store = ArtifactStore.active()
        shutil.rmtree(store.root, ignore_errors=True)

    try:
        fresh("s6r2_patch")
        half = json.loads(PRD_JSON)
        half["user_stories"], half["requirement_pool"] = [], []
        half["anything_unclear"] = ""                       # 合法空答：不该进补丁清单
        llm = FakeLLM([json.dumps(half, ensure_ascii=False),
                       json.dumps({"user_stories": ["US1"], "requirement_pool": [["P0", "core"]]},
                                  ensure_ascii=False)])
        out = _run(WritePRD(llm=llm), Message(content="做个 tinycli"))
        assert len(llm.calls) == 2, f"整问 1 次 + 定向补丁 1 次 = 2 次，实际 {len(llm.calls)}"
        patch_prompt = str(llm.calls[1])
        missing_seg = patch_prompt.split("## Missing fields")[1].split("## Context")[0]
        assert "user_stories" in missing_seg and "requirement_pool" in missing_seg
        assert "anything_unclear" not in missing_seg, "豁免字段漏进了补丁清单"
        assert "project_name" not in missing_seg, "补丁只许点名缺失字段，不许回抛整份 schema"
        ic = out.instruct_content
        assert ic["user_stories"] == ["US1"] and ic["requirement_pool"] == [["P0", "core"]]
        assert ic["project_name"], "首轮已填字段不能 merge 丢失"

        fresh("s6r2_stub")
        stub = json.loads(PRD_JSON)
        stub["competitive_analysis"] = []
        llm2 = FakeLLM([json.dumps(stub, ensure_ascii=False), '{"competitive_analysis": []}'])
        out2 = _run(WritePRD(llm=llm2), Message(content="做个 tinycli v2"))
        assert len(llm2.calls) == 1 + WritePRD().max_field_retries, \
            f"补不齐必须按重试轮封顶，实际 {len(llm2.calls)} 次"
        assert out2.instruct_content["project_name"], "截断后保留首轮结果，不空手而归"
    finally:
        for d in ("s6r2_patch", "s6r2_stub"):
            shutil.rmtree(rt.session_root(d), ignore_errors=True)
        rt.CURRENT_PROJECT.set("")
    print("  t20 R2 定向补丁在生产路径生效（点名缺字段/豁免合法空答/merge 保已填/封顶不活锁）")


def t21_single_structured_seam():
    """接线台账 #1 的 grep 断言：actions/ 下直连 `.llm.structured(` 归零——
    结构化输出的唯一出口是 BaseAction._ask（agent/plan_and_act/role_zero 三处非 Action 形态
    的直连在台账 #1 理由列登记，不属本检查范围）。"""
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "codeharness" / "actions").rglob("*.py")
                 if ".llm.structured(" in p.read_text(encoding="utf-8")]
    assert not offenders, f"绕过 _structured 接缝的 Action: {offenders}"
    print("  t21 actions/ 结构化调用唯一接缝（.llm.structured 直连=0）")


def t22_fixbug_rewrite_chain():
    """接线台账 #4/#5：FIX_BUG 全链——Engineer 按因装配（Agent.plans）先产重写计划，
    WriteCode 据此走 REFINED 分支：prompt 必须含源逐字段 "Code Plan And Change" 与
    use_inc 的 "The name of file to rewrite" 旧码置顶，产物覆盖回写，工单消费即删。"""
    import json
    import shutil
    import codeharness.runtime as rt
    import codeharness.team as T
    from codeharness.const import BUGFIX_FILENAME, DocName, RepoName, RequirementTag
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message

    CURRENT_PROJECT.set("s6fix")
    store = ArtifactStore.active()
    shutil.rmtree(store.root, ignore_errors=True)
    try:
        for sub, fn, body in ((RepoName.DOCS, DocName.REQUIREMENT, "做个 tinycli v2"),
                              (RepoName.DOCS, BUGFIX_FILENAME, "--version 参数直接崩溃"),
                              (RepoName.DOCS, DocName.DESIGN_JSON, '{"implementation_approach": "argparse"}'),
                              (RepoName.DOCS, DocName.TASKS,
                               '{"task_list": [{"filename": "cli.py", "instruction": "修 version"}]}'),
                              (RepoName.SRC, "cli.py", "print('old')")):
            asyncio.run(store.save(sub, Document(filename=fn, content=body)))
        plan = json.dumps({"development_plan": ["修复 cli.py 的 version 分支"],
                           "incremental_change": ["```diff\n+import sys\n```"]})
        llm = FakeLLM([plan,                                             # PlanAndChange
                       "```python\nimport sys\nVERSION = '0.1'\n```",    # WriteCode(REFINED)
                       "## Code Review Result\nLGTM",                    # WriteCodeReview
                       "摘要：修复 version 分支"])                        # SummarizeCode
        eng = T.classic_team(llm)["Engineer"]
        trig = Message(content="--version 崩溃", role="assistant",
                       cause_by=RequirementTag.FIX_BUG, sent_from="PM",
                       instruct_content={"issue_filename": BUGFIX_FILENAME}, instruct_schema="IssueDetail")
        out = asyncio.run(eng.build().ainvoke(
            {"name": "Engineer", "inbox": [trig], "memory": [], "action_cursor": -1,
             "chosen": "", "loops": 0, "output": []}))
        causes = [m.cause_by for m in out["output"]]
        assert causes == ["WriteCodePlanAndChange", "WriteCode", "WriteCodeReview", "SummarizeCode"], \
            f"FIX_BUG 按因装配没通电: {causes}"
        pac_doc = (store.root / RepoName.DOCS / DocName.CODE_PLAN_AND_CHANGE).read_text(encoding="utf-8")
        assert "Development Plan" in pac_doc and "修复 cli.py 的 version 分支" in pac_doc
        wc_prompt = str(llm.calls[1])
        assert "## Code Plan And Change" in wc_prompt, "REFINED 模板没被使用（源 :128-142 分支断）"
        assert "The name of file to rewrite: `cli.py`" in wc_prompt, "use_inc 旧码置顶缺失（源 :207）"
        # 旧码本体也必须在（str() 会把消息里的引号转义，断无引号片段）；REFINED 主指令逐字段
        assert "print(" in wc_prompt and "to complete incremental development" in wc_prompt
        assert (store.root / RepoName.SRC / "cli.py").read_text(encoding="utf-8").startswith("import sys"), \
            "重写产物没回写"
        assert not (store.root / RepoName.DOCS / BUGFIX_FILENAME).exists(), "工单没有消费即删"
    finally:
        shutil.rmtree(rt.session_root("s6fix"), ignore_errors=True)
        rt.CURRENT_PROJECT.set("")
    print("  t22 FIX_BUG 全链：按因产计划→REFINED 重写→评审→摘要，旧码置顶进 prompt、工单消费即删")


def main():
    checks = [t1_prompts_verbatim, t2_prompt_imports_and_consumers,
              t3_write_prd_three_branches, t4_action_templates_verbatim,
              t5_write_design_branches, t6_write_tasks_requirements, t7_run_code_summary,
              t8_prepare_documents_instruct, t9_write_code_three_contexts,
              t10_write_code_review_rounds, t11_action_prompts_verbatim,
              t12_graph_store_roundtrip, t13_class_view_pipeline,
              t14_rebuild_class_view_action, t15_research_three_legs,
              t16_search_and_summarize_history, t17_role_profile_parity,
              t18_strategy_switch, t19_ext_api_acceptance,
              t20_structured_patch_live, t21_single_structured_seam,
              t22_fixbug_rewrite_chain, t23_runcode_triage]
    for c in checks:
        c()
    print(f"\nS6 门禁通过：{len(checks)} 组 —— 批1 prompt 逐字 2 组 + 批2a 十件 9 组 + 批2c 存储/类图 3 组"
          f"+ 批2b 两件 2 组 + 批3-5 对账/策略/扩展点 3 组 + 接线批 R2 生产化 2 组 + 增量重写链 1 组 + 分诊 1 组")


def t23_runcode_triage():
    """接线台账 #6：RunCode 按复盘 Send To 分诊（源 qa_engineer.py:124-148）——
    Engineer → 具名投递且 instruct 恰好是 CodingContext 形态；QaEngineer → <self> 且
    instruct 恰好是 RunCodeContext 形态（extra=forbid：多一键=回路静默断，十二处教训的续集）；
    DebugError 修复产物进 **tests/**（源 :155），不再污染 SRC/。"""
    import shutil
    from codeharness.actions.debug_error import DebugError
    from codeharness.actions.run_code import RunCode
    from codeharness.const import MESSAGE_ROUTE_TO_SELF, RepoName
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message, RunCodeContext

    def ctx_msg(store, code_fn="app.py"):
        (store.root / RepoName.SRC).mkdir(parents=True, exist_ok=True)
        (store.root / RepoName.SRC / code_fn).write_text("def f():\n    return 1 + 1\n", encoding="utf-8")
        (store.root / RepoName.TESTS).mkdir(parents=True, exist_ok=True)
        (store.root / RepoName.TESTS / f"test_{code_fn.split('.')[0]}.py").write_text(
            "def t():\n    assert False\n", encoding="utf-8")
        c = RunCodeContext(code_filename=code_fn, test_filename=f"test_{code_fn.split('.')[0]}.py")
        # command 留空 → RunCode 默认 pytest tests -x（真跑必败的 fixture，零 mock）
        return Message(content="跑", instruct_content=c.model_dump(), instruct_schema="RunCodeContext")

    store = _fixture_store("s6tri")
    try:
        # (a) Send To: Engineer → 具名投给开发，instruct=CodingContext 形态
        rc = RunCode(llm=FakeLLM(["## instruction:\n实现有误\n## File To Rewrite:\napp.py\n"
                                  "## Status:\nFAIL\n## Send To:\nEngineer"]))
        out = asyncio.run(rc.run(ctx_msg(store)))
        assert out.send_to == {"Engineer"}, out.send_to
        assert out.instruct_schema == "CodingContext" and set(out.instruct_content) == {"filename"}, \
            out.instruct_content
        from codeharness.schema import CodingContext
        CodingContext(**out.instruct_content)            # 消费方形态验证：不 ValidationError 才算通
        assert "Send To:" in out.content and "Engineer" in out.content

        # (b) Send To: QaEngineer → <self> 自环，instruct=RunCodeContext 形态
        rc2 = RunCode(llm=FakeLLM(["## instruction:\n断言写错\n## File To Rewrite:\ntest_app.py\n"
                                   "## Status:\nFAIL\n## Send To:\nQaEngineer"]))
        out2 = asyncio.run(rc2.run(ctx_msg(store)))
        assert out2.send_to == {MESSAGE_ROUTE_TO_SELF}, out2.send_to
        assert out2.instruct_schema == "RunCodeContext"
        RunCodeContext(**out2.instruct_content)

        # (c) DebugError 修测试：产物进 tests/、SRC 不动、回流 <self>（(b) 已把失败输出存成 test_app.py.json）
        dbg_src_before = (store.root / RepoName.SRC / "app.py").read_text(encoding="utf-8")
        fix_llm = FakeLLM(["## file name of the code to rewrite: test_app.py\n"
                           "```python\ndef t():\n    assert 1 == 1\n```"])
        ctx = RunCodeContext(code_filename="app.py", test_filename="test_app.py",
                             output_filename="test_app.py.json",
                             working_directory=str(store.root))
        dbg = asyncio.run(DebugError(llm=fix_llm).run(
            Message(content="修", instruct_content=ctx.model_dump(), instruct_schema="RunCodeContext")))
        assert (store.root / RepoName.TESTS / "test_app.py").read_text(encoding="utf-8").strip() \
            .startswith("def t():"), "修复产物没进 tests/"
        assert (store.root / RepoName.SRC / "app.py").read_text(encoding="utf-8") == dbg_src_before, \
            "修测试不该动 SRC"
        assert dbg.send_to == {MESSAGE_ROUTE_TO_SELF} and dbg.instruct_schema == "RunCodeContext"
        RunCodeContext(**dbg.instruct_content)
        assert dbg.instruct_content["test_filename"] == "test_app.py"
    finally:
        shutil.rmtree(_ws("s6tri"), ignore_errors=True)
        CURRENT_PROJECT.set("")
    print("  t23 RunCode 分诊两分支 + instruct 契约形态 + DebugError 写回 tests/（源 qa_engineer 语义）")


if __name__ == "__main__":
    sys.exit(main())
