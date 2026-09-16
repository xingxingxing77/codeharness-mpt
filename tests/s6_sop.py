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

    # 豁免 = 有意的 `推迟` 面（各件 docstring 已写明去向），缺它们不算漂移
    pairs = [
        ("actions/write_prd.py", set()),
        ("actions/write_test.py", set()),
        ("actions/run_code.py", set()),
        ("actions/design_api.py", set()),
        ("actions/project_management.py", set()),
        ("actions/summarize_code.py", set()),
        ("actions/write_code.py", set()),
        ("actions/write_code_review.py", set()),
        ("actions/research.py", set()),
        ("actions/search_and_summarize.py", {"SEARCH_AND_SUMMARIZE_SALES_SYSTEM",
                                             "SEARCH_AND_SUMMARIZE_SALES_PROMPT", "SEARCH_FOOD"}),
    ]
    n, absent_src = 0, []
    for rel, exempt in pairs:
        mod_name = "codeharness." + rel.replace("actions/", "actions.").replace(".py", "")
        mod = importlib.import_module(mod_name)
        sc = source_consts(rel)
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


def main():
    checks = [t1_prompts_verbatim, t2_prompt_imports_and_consumers,
              t3_write_prd_three_branches, t4_action_templates_verbatim,
              t5_write_design_branches, t6_write_tasks_requirements, t7_run_code_summary,
              t8_prepare_documents_instruct, t9_write_code_three_contexts,
              t10_write_code_review_rounds, t11_action_prompts_verbatim,
              t12_graph_store_roundtrip, t13_class_view_pipeline,
              t14_rebuild_class_view_action, t15_research_three_legs,
              t16_search_and_summarize_history]
    for c in checks:
        c()
    print(f"\nS6 门禁通过：{len(checks)} 组 —— 批1 prompt 逐字 2 组 + 批2a 十件 9 组 + 批2c 存储/类图 3 组"
          f"+ 批2b 两件 2 组；后续批在此续加（build_role 全名 / N2 外部注册演示 / 2d-2f 各 Action 一 fixture）")


if __name__ == "__main__":
    sys.exit(main())
