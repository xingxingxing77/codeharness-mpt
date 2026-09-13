"""项目入口：源 team.py 的 run_project(:102) + run(:123) 合体。
run_project = 脚本场景的 async generator；prepare_project = runner 专用的三件套。"""
from codeharness.schema import Message
from codeharness.const import RequirementTag


def default_team(llm, cost_manager=None, env_desc: str = "a software company"):
    """= 源 software_company.py 组队（RoleZero 系，参考速查 §4）；经典线组队见 第 9 步 §4"""
    from codeharness.roles.role_zero import RoleZero
    from codeharness.prompts.role_zero import SYSTEM_PROMPT
    from codeharness.tools import REGISTRY
    profiles = {                                    # 字段逐字抄自 roles/ 对应文件（参考速查 §4）
        "Mike":  ("Team Leader", "lead a team to fulfill requirements efficiently"),
        "Alice": ("Product Manager", "Create a Product Requirement Document or market research"),
        "Bob":   ("Architect", "design a concise, usable, complete software system"),
    }
    return {name: RoleZero({"name": name, "profile": prof, "goal": goal},
                           REGISTRY, llm, system_prompt=SYSTEM_PROMPT, env_desc=env_desc)
            for name, (prof, goal) in profiles.items()}


async def run_project(idea: str, project_id: str, investment: float | None = None,
                      agents: dict | None = None, checkpointer=None):
    """async generator：产出 astream_events 事件（脚本场景）。runner 用 prepare_project。"""
    if agents is None:
        agents = default_team(llm=_make_llm(cost_manager))
    from codeharness.environment.team_graph import build_team
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0,
            "budget_used": 0.0, "debug_rounds": 0, "finished": False}
    async for ev in team.astream_events(init, config, version="v2"):
        yield ev


def prepare_project(idea: str, project_id: str, investment: float | None = None,
                    agents: dict | None = None, checkpointer=None):
    """runner 专用（第 10 步 §3.5）：返回 (graph, config, init) 三件套，由 runner 自己驱动 astream——
    interrupt resume 必须持有同一 graph 实例与 thread_id。"""
    from codeharness.environment.team_graph import build_team
    from codeharness.provider.gateway import LLMGateway
    from codeharness.provider.cost import CostManager
    from codeharness.configs.settings import settings
    if agents is None:
        agents = default_team(LLMGateway(
            cost_manager=CostManager(max_budget=investment or settings.max_budget)))
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0,
            "budget_used": 0.0, "debug_rounds": 0, "finished": False}
    return team, config, init


def _make_llm(cost_manager=None):
    from codeharness.provider.gateway import LLMGateway
    from codeharness.configs.settings import settings
    from codeharness.provider.cost import CostManager
    return LLMGateway(cost_manager=cost_manager or CostManager(max_budget=settings.max_budget))


def classic_team(llm):
    """经典 Role 线五角色（参考速查全图）。每个 Agent 的 actions 即第 9 步的实现类。"""
    from codeharness.roles.agent import Agent
    from codeharness.actions.write_prd import WritePRD
    from codeharness.actions.project_management import WriteTasks
    from codeharness.actions.write_code import WriteCode
    from codeharness.actions.write_test import WriteTest
    from codeharness.actions.run_code import RunCode
    from codeharness.actions.debug_error import DebugError
    from codeharness.actions.summarize_code import SummarizeCode
    return {
        "PM":        Agent({"name": "PM", "profile": "Product Manager",
                            "goal": "write a PRD"}, [WritePRD(llm=llm)], llm, max_loops=2),
        "PMManager": Agent({"name": "PMManager", "profile": "Project Manager",
                            "goal": "break down tasks"}, [WriteTasks(llm=llm)], llm, max_loops=2),
        "Engineer":  Agent({"name": "Engineer", "profile": "Engineer", "goal": "write code"},
                           [WriteCode(llm=llm), SummarizeCode(llm=llm)], llm, max_loops=4),
        "QA":        Agent({"name": "QA", "profile": "QA Engineer", "goal": "test the code"},
                           [WriteTest(llm=llm), RunCode(llm=llm), DebugError(llm=llm)], llm,
                           react_mode="REACT", max_loops=5),
    }
