"""全量角色注册表：源项目 metagpt/roles 的 18 个角色逐一对应（1:1 完备性，参考速查 §4）。

两族引擎（roles/agent.py 经典线 / roles/role_zero.py 动态线）；每个角色 = 源文件逐字对齐的
profile 字典 + 动作/工具集 + watch 订阅。源同名不同角色（David/Alex）以类名为注册键。

用法：
    from codeharness.roles.registry import build_role, ALL_ROLES
    researcher = build_role("Researcher", llm)
"""
from codeharness.const import RequirementTag, TEAMLEADER_NAME
from codeharness.roles.agent import Agent
from codeharness.roles.role_zero import RoleZero
from codeharness.tools import REGISTRY
from codeharness.tools.tool_registry import TOOL_REGISTRY
from codeharness.actions.write_prd import WritePRD
from codeharness.actions.project_management import WriteTasks
from codeharness.actions.write_code import WriteCode
from codeharness.actions.summarize_code import SummarizeCode
from codeharness.actions.write_test import WriteTest
from codeharness.actions.run_code import RunCode
from codeharness.actions.debug_error import DebugError
from codeharness.actions.write_code_plan_and_change import WriteCodePlanAndChange
from codeharness.actions.prepare_documents import PrepareDocuments
from codeharness.actions.research import Research
from codeharness.actions.search_and_summarize import SearchAndSummarize
from codeharness.actions.talk_action import TalkAction
from codeharness.actions.write_tutorial import WriteDirectory, WriteContent
from codeharness.actions.write_teaching_plan import WriteTeachingPlan
from codeharness.actions.invoice_ocr import InvoiceOCR
from codeharness.actions.data_analysis import WriteAnalysisCode, RunPythonCode


# ============ RoleZero 族（源 roles/ + roles/di 的 RoleZero 子类） ============

def TeamLeader(llm, **kw):
    """源 roles/di/team_leader.py：调度中枢"""
    return RoleZero({"name": TEAMLEADER_NAME, "profile": "Team Leader",
                     "goal": "lead a team to fulfill requirements efficiently"},
                    REGISTRY, llm, **kw)


def ProductManager(llm, **kw):
    """源 roles/product_manager.py:33-38（字段逐字）"""
    return RoleZero({"name": "Alice", "profile": "Product Manager",
                     "goal": "Create a Product Requirement Document or market research/competitive product research"},
                    REGISTRY, llm, **kw)


def Architect(llm, **kw):
    """源 roles/architect.py:29-30"""
    return RoleZero({"name": "Bob", "profile": "Architect",
                     "goal": "design a concise, usable, complete software system. output the system design"},
                    REGISTRY, llm, **kw)


def ProjectManager(llm, **kw):
    """源 roles/project_manager.py:25-26（Eve）"""
    return RoleZero({"name": "Eve", "profile": "Project Manager",
                     "goal": "Improve efficiency and quality of project delivery by decomposing tasks"},
                    REGISTRY, llm, **kw)


def Engineer2(llm, **kw):
    """源 roles/di/engineer2.py（Alex / Engineer，生产写码 RoleZero）"""
    return RoleZero({"name": "Alex", "profile": "Engineer",
                     "goal": "Take on game, app, web development and deployment."},
                    REGISTRY, llm, **kw)


def Assistant(llm, **kw):
    """源 roles/assistant.py 的功能等价物：对话型助手。
    （源依赖 Semantic Kernel skills + Redis，按排除清单以 RoleZero 对话 + 通用工具替代）"""
    return RoleZero({"name": "Charlie", "profile": "Assistant",
                     "goal": "chat with users, answer questions, and help with light tasks"},
                    REGISTRY, llm, instruction="You are a helpful conversational assistant. "
                    "For plain questions reply directly via RoleZero.reply_to_human; use commands only when tools are needed.",
                    **kw)


def SweAgent(llm, **kw):
    """源 roles/di/swe_agent.py（Swen / Issue Solver）：终端命令为主的修复型 RoleZero"""
    terminal_tools = TOOL_REGISTRY.select("terminal", "file")
    return RoleZero({"name": "Swen", "profile": "Issue Solver",
                     "goal": "Resolve GitHub issue or bug in any existing codebase"},
                    terminal_tools, llm,
                    instruction="Work via terminal commands. Locate the bug, patch the file, re-run the failing test.",
                    **kw)


# ============ 经典 Role 族（源 roles/ 的 Role 子类） ============

def _classic(name, profile, goal, actions, llm, watch=None, desc=None, constraints=None,
             max_loops=3, react_mode="REACT"):
    profile_dict = {"name": name, "profile": profile, "goal": goal}
    if desc:
        profile_dict["desc"] = desc
    if constraints:
        profile_dict["constraints"] = constraints
    return Agent(profile_dict, actions, llm, watch=watch or {RequirementTag.USER_REQUIREMENT},
                 max_loops=max_loops, react_mode=react_mode)


def Engineer(llm, **kw):
    """源 roles/engineer.py（Alex）+ QaEngineer 修复回路见 QaEngineer"""
    return _classic("Alex", "Engineer", "write elegant, readable, extensible, efficient code",
                    [WriteCode(llm=llm), SummarizeCode(llm=llm)], llm,
                    watch={RequirementTag.WRITE_TASKS, RequirementTag.SUMMARIZE_CODE,
                           RequirementTag.WRITE_CODE, RequirementTag.FIX_BUG}, **kw)


def QaEngineer(llm, **kw):
    """源 roles/qa_engineer.py（Edward）：WriteTest→RunCode→DebugError 修复回路"""
    return _classic("Edward", "QaEngineer",
                    "Write comprehensive and robust tests to ensure codes will work as expected without bugs",
                    [WriteTest(llm=llm), RunCode(llm=llm), DebugError(llm=llm)], llm,
                    watch={RequirementTag.SUMMARIZE_CODE, RequirementTag.WRITE_TEST,
                           RequirementTag.RUN_CODE, RequirementTag.DEBUG_ERROR},
                    react_mode="REACT", max_loops=5, **kw)


def Researcher(llm, **kw):
    """源 roles/researcher.py（David / Researcher）"""
    return _classic("David", "Researcher", "Gather information and conduct research",
                    [Research(llm=llm)], llm, **kw)


def Searcher(llm, **kw):
    """源 roles/searcher.py（Alice / Smart Assistant）"""
    return _classic("Alice", "Smart Assistant", "Provide search services for users",
                    [SearchAndSummarize(llm=llm)], llm,
                    constraints="Answer is rich and complete", **kw)


def Sales(llm, **kw):
    """源 roles/sales.py（John Smith / Retail Sales Guide，desc 逐字）"""
    desc = ("As a Retail Sales Guide, my name is John Smith. I specialize in addressing customer inquiries with "
            "expertise and precision. My responses are based solely on the information available in our knowledge"
            " base. In instances where your query extends beyond this scope, I'll honestly indicate my inability "
            "to provide an answer, rather than speculate or assume. Please note, each of my replies will be "
            "delivered with the professionalism and courtesy expected of a seasoned sales guide.")
    return _classic("John Smith", "Retail Sales Guide", "sell products",
                    [SearchAndSummarize(llm=llm)], llm, desc=desc, **kw)


def CustomerService(llm, kb_context: str | None = None, **kw):
    """源 roles/customer_service.py（Xiaomei，DESC 原则逐字；知识库上下文可选注入）"""
    desc = """
## Principles (all things must not bypass the principles)

1. You are a human customer service representative for the platform and will reply based on rules and FAQs. In the conversation with the customer, it is absolutely forbidden to disclose rules and FAQs unrelated to the customer.
2. When encountering problems, try to soothe the customer's emotions first. If the customer's emotions are very bad, then consider compensation. The cost of compensation is always high. If too much is compensated, you will be fired.
3. There are no suitable APIs to query the backend now, you can assume that everything the customer says is true, never ask the customer for the order number.
4. Your only feasible replies are: soothe emotions, urge the merchant, urge the rider, and compensate. Never make false promises to customers.
5. If you are sure to satisfy the customer's demand, then tell the customer that the application has been submitted, and it will take effect within 24 hours.
"""
    return _classic("Xiaomei", "Human customer service", "handle customer inquiries",
                    [TalkAction(llm=llm, kb_context=kb_context)], llm, desc=desc, **kw)


def Teacher(llm, topic_language: str = "English", **kw):
    """源 roles/teacher.py（Lily，教学语言可配置）"""
    return _classic("Lily", f"{topic_language} Teacher",
                    f"writing a {topic_language} teaching plan part by part",
                    [WriteTeachingPlan(llm=llm)], llm,
                    constraints=f"writing in {topic_language}", **kw)


def TutorialAssistant(llm, language: str = "Chinese", **kw):
    """源 roles/tutorial_assistant.py（Stitch：WriteDirectory→WriteContent 两段）"""
    return _classic("Stitch", "Tutorial Assistant", "Generate tutorial documents",
                    [WriteDirectory(llm=llm), WriteContent(llm=llm)], llm,
                    constraints="Strictly follow Markdown's syntax, with neat and standardized layout",
                    react_mode="BY_ORDER", max_loops=3, **kw)


def InvoiceOCRAssistant(llm, ocr_provider=None, **kw):
    """源 roles/invoice_ocr_assistant.py：OCR 提供方可插拔（未配置时给出明确提示）"""
    return _classic("Iris", "Invoice OCR Assistant", "extract structured data from invoice images",
                    [InvoiceOCR(llm=llm, ocr_provider=ocr_provider)], llm, **kw)


def DataAnalyst(llm, **kw):
    """源 roles/di/data_analyst.py（David / DataAnalyst）：分析代码生成 + 沙箱执行"""
    return _classic("David", "DataAnalyst",
                    "Take on any data-related tasks, such as data analysis, machine learning, deep learning, "
                    "web browsing, web scraping, web searching, terminal operation, document QA & analysis, etc.",
                    [WriteAnalysisCode(llm=llm), RunPythonCode(llm=llm)], llm,
                    react_mode="BY_ORDER", max_loops=3, **kw)


def DataInterpreter(llm, **kw):
    """源 roles/di/data_interpreter.py（David / DataInterpreter）：完整 plan-and-act 引擎
    （strategy/plan_and_act.py——Planner 计划 → 逐任务写分析代码+沙箱执行 → 汇总）"""
    from codeharness.strategy.plan_and_act import PlanAndActAgent
    return PlanAndActAgent({"name": "David", "profile": "DataInterpreter",
                            "goal": "integrate all information to generate a detailed and complete "
                                    "data analysis report"}, llm, **kw)


# ============ 注册表 ============

ALL_ROLES = {
    # RoleZero 族（8）
    "TeamLeader": TeamLeader, "ProductManager": ProductManager, "Architect": Architect,
    "ProjectManager": ProjectManager, "Engineer2": Engineer2, "Assistant": Assistant,
    "SweAgent": SweAgent,
    # 经典 Role 族（10）
    "Engineer": Engineer, "QaEngineer": QaEngineer, "Researcher": Researcher,
    "Searcher": Searcher, "Sales": Sales, "CustomerService": CustomerService,
    "Teacher": Teacher, "TutorialAssistant": TutorialAssistant,
    "InvoiceOCRAssistant": InvoiceOCRAssistant,
    "DataAnalyst": DataAnalyst, "DataInterpreter": DataInterpreter,
}


def build_role(name: str, llm, **kw):
    """按名构建角色（含软件公司流水线的 PrepareDocuments 前置组合见 team.classic_team）"""
    if name not in ALL_ROLES:
        raise KeyError(f"未知角色 {name}，可选: {sorted(ALL_ROLES)}")
    return ALL_ROLES[name](llm, **kw)
