"""项目入口：源 team.py 的 run_project(:102) + run(:123) 合体。
run_project = 脚本场景的 async generator；prepare_project = runner 专用的三件套。"""
from codeharness.schema import Message
from codeharness.const import RequirementTag, TEAMLEADER_NAME


def default_team(llm, env_desc: str = "a software company"):
    """= 源 software_company.py 组队（RoleZero 系，参考速查 §4）；经典线组队见 第 9 步 §4。
    ⚠ 这套角色名不在 `team_graph.SOP` 里，动态范式要靠它自己的路由表（S6 接），
    所以现在**不参与默认兜底**——默认见 `_default_agents`。"""
    from codeharness.roles.role_zero import RoleZero
    from codeharness.prompts.role_zero import SYSTEM_PROMPT
    from codeharness.configs.settings import settings
    from codeharness.memory.brain_memory import BrainMemory
    from codeharness.tools import REGISTRY
    ltm = None
    if settings.enable_rag:                                 # 这个开关此前零读者，现在真管记忆召回
        from codeharness.memory.longterm import LongTermMemory
        from codeharness.provider.gateway import LLMGateway
        ltm = LongTermMemory(embeddings=LLMGateway.embeddings())   # project 用时现取，三角色共用
    profiles = {                                    # 字段逐字抄自 roles/ 对应文件（参考速查 §4）
        TEAMLEADER_NAME: ("Team Leader", "Manage a team to assist users"),   # t17 对账：源逐字
        "Alice": ("Product Manager", "Create a Product Requirement Document or market research"),
        "Bob":   ("Architect", "design a concise, usable, complete software system"),
    }
    # 每角色一个 brain：key 按角色名分（RoleZero._brain_key），Redis 挂了也只是不摘要，不影响跑
    return {name: RoleZero({"name": name, "profile": prof, "goal": goal},
                           REGISTRY, llm, system_prompt=SYSTEM_PROMPT, env_desc=env_desc,
                           brain=BrainMemory(), longterm_memory=ltm)
            for name, (prof, goal) in profiles.items()}


async def run_project(idea: str, project_id: str, agents: dict | None = None,
                      checkpointer=None, cost_manager=None):
    """async generator：产出 astream_events 事件（脚本场景）。runner 用 prepare_project。"""
    if agents is None:
        agents = _default_agents(cost_manager)
    from codeharness.environment.team_graph import build_team
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0, "debug_rounds": 0, "finished": False}
    async for ev in team.astream_events(init, config, version="v2"):
        yield ev


def prepare_project(idea: str, project_id: str, agents: dict | None = None,
                    checkpointer=None, cost_manager=None):
    """runner 专用（第 10 步 §3.5）：返回 (graph, config, init) 三件套，由 runner 自己驱动 astream——
    interrupt resume 必须持有同一 graph 实例与 thread_id。

    ⚠ `cost_manager` 必须由调用方建好传进来：图内部各角色的 LLM 共用这一个实例，
    调用方手上的另一个实例只会记到 0（这个断链曾让前端用量恒为 0）。"""
    from codeharness.environment.team_graph import build_team
    if agents is None:
        agents = _default_agents(cost_manager)
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0, "debug_rounds": 0, "finished": False}
    return team, config, init


def _make_llm(cost_manager=None):
    from codeharness.provider.gateway import LLMGateway
    from codeharness.provider.cost import CostManager
    return LLMGateway(cost_manager=cost_manager or CostManager())


def _default_agents(cost_manager=None):
    """不传 agents 时的兜底组队。必须是经典线：`build_team` 默认用的就是 `team_graph.SOP`，
    角色名对不上时 LangGraph 只会打一行 "Ignoring unknown node name PM"，
    整场会话零次 LLM 调用（实测，runner 走的就是这条兜底）。"""
    return classic_team(_make_llm(cost_manager))


def classic_team(llm):
    """经典 Role 线五角色（参考速查全图）。每个 Agent 的 actions 即第 9 步的实现类。

    ⚠ watch 集合逐个对齐 `team_graph.SOP` 的入边（真模型第十一处的根因，s3b t11 双向钉住）：
    `Agent._observe` 默认只订阅 UserRequirement——缺 watch 的角色把路由进来的消息整条丢掉，
    退化成拿空记忆干活（上游产物没了、内容全靠模型编），Engineer 则空 filename 一路炸到崩。
    测试手写了 watch 而生产组队漏写，正是「两套表必须互洽」教训的又一处。"""
    from codeharness.roles.agent import Agent
    from codeharness.actions.write_prd import WritePRD
    from codeharness.actions.design_api import WriteDesign
    from codeharness.actions.project_management import WriteTasks
    from codeharness.actions.write_code import WriteCode
    from codeharness.actions.write_test import WriteTest
    from codeharness.actions.run_code import RunCode
    from codeharness.actions.debug_error import DebugError
    from codeharness.actions.summarize_code import SummarizeCode
    return {
        "PM":        Agent({"name": "PM", "profile": "Product Manager",
                            "goal": "write a PRD"}, [WritePRD(llm=llm)], llm, max_loops=2),
        "Architect": Agent({"name": "Architect", "profile": "Architect",
                            "goal": "design a concise, usable, complete software system"},
                           [WriteDesign(llm=llm)], llm, max_loops=2,
                           watch={RequirementTag.WRITE_PRD}),
        "PMManager": Agent({"name": "PMManager", "profile": "Project Manager",
                            "goal": "break down tasks"}, [WriteTasks(llm=llm)], llm, max_loops=2,
                           watch={RequirementTag.WRITE_DESIGN}),
        "Engineer":  Agent({"name": "Engineer", "profile": "Engineer", "goal": "write code"},
                           [WriteCode(llm=llm), SummarizeCode(llm=llm)], llm,
                           react_mode="BY_ORDER", max_loops=4,
                           watch={RequirementTag.WRITE_TASKS,
                                  RequirementTag.WRITE_CODE_PLAN_AND_CHANGE,
                                  RequirementTag.FIX_BUG, RequirementTag.DEBUG_ERROR}),
        "QA":        Agent({"name": "QA", "profile": "QA Engineer", "goal": "test the code"},
                           [WriteTest(llm=llm), RunCode(llm=llm), DebugError(llm=llm)], llm,
                           react_mode="REACT", max_loops=5,
                           watch={RequirementTag.SUMMARIZE_CODE}),
    }
