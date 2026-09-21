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
    agents = {name: RoleZero({"name": name, "profile": prof, "goal": goal},
                             REGISTRY, llm, system_prompt=SYSTEM_PROMPT, env_desc=env_desc,
                             brain=BrainMemory(), longterm_memory=ltm)
              for name, (prof, goal) in profiles.items()}
    # 委派名册（C1-②）：源 `_get_team_info`(:50-57) 的等价。缺了它模型无从知道成员叫什么，
    # 只能编名字——编出来的名字在 `publish_team_message` 处被拒（有 roster 才校验），
    # 侥幸投出去也会在 route 抛 UnknownRecipient。名册先于委派存在。
    roster = {n: f"{r.profile.get('profile', '')}, {r.profile.get('goal', '')}"
                for n, r in agents.items()}
    for r in agents.values():
        r.teammates = roster
    if TEAMLEADER_NAME in agents:
        # 源 TeamLeader._think(:66-67) 每轮重算 instruction=TL_INSTRUCTION；本仓走 instruction_provider
        from codeharness.prompts.di.team_leader import TL_INSTRUCTION
        leader = agents[TEAMLEADER_NAME]
        leader.instruction_provider = lambda: TL_INSTRUCTION.format(team_info=leader.team_info())
    return agents


async def run_project(idea: str, project_id: str, agents: dict | None = None,
                      checkpointer=None, cost_manager=None):
    """async generator：产出 astream_events 事件（脚本场景）。runner 用 prepare_project。"""
    if agents is None:
        agents = _default_agents(cost_manager)
    from codeharness.environment.team_graph import build_team
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "round": 0, "debug_rounds": 0, "team_rounds": 0, "finished": False}
    async for ev in team.astream_events(init, config, version="v2"):
        yield ev


def prepare_project(idea: str, project_id: str, agents: dict | None = None,
                    checkpointer=None, cost_manager=None, sop: dict | None = None):
    """runner 专用（第 10 步 §3.5）：返回 (graph, config, init) 三件套，由 runner 自己驱动 astream——
    interrupt resume 必须持有同一 graph 实例与 thread_id。

    ⚠ `cost_manager` 必须由调用方建好传进来：图内部各角色的 LLM 共用这一个实例，
    调用方手上的另一个实例只会记到 0（这个断链曾让前端用量恒为 0）。
    `sop` 传 None 用经典线路由表；动态线（S9.1 对照）传 `dynamic_assembly` 的表。"""
    from codeharness.environment.team_graph import build_team
    if agents is None:
        agents = _default_agents(cost_manager)
    team = build_team(agents, checkpointer=checkpointer, sop=sop)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "round": 0, "debug_rounds": 0, "team_rounds": 0, "finished": False}
    return team, config, init


def dynamic_assembly(llm):
    """S9.1 同范式对照·本仓侧装配（台账 #19①）：default_team 三角色 + 动态路由表。
    需求只喂队长（TEAMLEADER_NAME=源逐字 "Mike"）；队长再按 `TeamLeader.publish_team_message`
    把任务定向投给 Alice/Bob（C1-②，`route` 的具名投递通路）。仍差源的一半：成员干完不回报队长，
    所以队长看不到"谁做完了"，源 MGX 的"跟踪进度并 finish_current_task"还没接上（②b）。"""
    from codeharness.const import RequirementTag, TEAMLEADER_NAME
    return default_team(llm), {RequirementTag.USER_REQUIREMENT: [TEAMLEADER_NAME]}


def react_assembly(llm):
    """9.2 策略曲线第三腿（N3）：经典队形全员 REACT 执行循环——build_role(strategy="react")
    的组队版（roles/registry.py:246 同款换装语义，集中到装配面）。路由表仍用经典 SOP：
    换的是执行方式，不是编排。"""
    agents = classic_team(llm)
    for r in agents.values():
        r.react_mode = "REACT"
    return agents


def _make_llm(cost_manager=None, override: dict | None = None):
    """会话级模型覆盖。**只认 `model` 一个键**：base_url / api_key 若也吃客户端输入，
    等于让请求方指定任意端点（SSRF 面）；模型名只会送到已配置的那个端点，最坏 4xx。"""
    from codeharness.configs.settings import settings
    from codeharness.provider.gateway import LLMGateway
    from codeharness.provider.cost import CostManager
    model = str((override or {}).get("model") or "").strip()
    cfg = settings.llm.model_copy(update={"model": model}) \
        if model and model != settings.llm.model else None
    return LLMGateway(cfg=cfg, cost_manager=cost_manager or CostManager())


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
    from codeharness.actions.prepare_documents import PrepareDocuments
    from codeharness.actions.design_api import WriteDesign
    from codeharness.actions.project_management import WriteTasks
    from codeharness.actions.write_code import WriteCode
    from codeharness.actions.write_code_review import WriteCodeReview
    from codeharness.actions.write_code_plan_and_change import WriteCodePlanAndChange
    from codeharness.actions.write_test import WriteTest
    from codeharness.actions.run_code import RunCode
    from codeharness.actions.debug_error import DebugError
    from codeharness.actions.summarize_code import SummarizeCode
    return {
        # PM 前置 PrepareDocuments 照源 product_manager.py:45-46 的固定 SOP（BY_ORDER 表达），
        # requirements_filename 从此有生产者；Engineer 的 WriteCodeReview 照源 engineer.py:128-137
        # "每写完一文件即评审"，游标顺序 = 业务顺序（接线台账 #2/#3 收口，e2e 手写装配的同款形态）。
        "PM":        Agent({"name": "PM", "profile": "Product Manager",
                            "goal": "write a PRD"},
                           [PrepareDocuments(llm=llm), WritePRD(llm=llm)], llm,
                           react_mode="BY_ORDER", max_loops=3),
        "Architect": Agent({"name": "Architect", "profile": "Architect",
                            "goal": "design a concise, usable, complete software system"},
                           [WriteDesign(llm=llm)], llm, max_loops=2,
                           watch={RequirementTag.WRITE_PRD}),
        "PMManager": Agent({"name": "PMManager", "profile": "Project Manager",
                            "goal": "break down tasks"}, [WriteTasks(llm=llm)], llm, max_loops=2,
                           watch={RequirementTag.WRITE_DESIGN}),
        "Engineer":  Agent({"name": "Engineer", "profile": "Engineer", "goal": "write code"},
                           [WriteCode(llm=llm), WriteCodeReview(llm=llm), SummarizeCode(llm=llm),
                            WriteCodePlanAndChange(llm=llm)], llm,
                           react_mode="BY_ORDER", max_loops=6,
                           # 源 engineer._new_code_actions(:455-487) 的按因装配浓缩：
                           # FIX_BUG 工单 → 先产重写计划，WriteCode 据此走 REFINED；其余触发保持
                           # 写→评审→摘要默认序（PlanAndChange 不在 default_plan，正常线不多烧一次模型）。
                           plans={RequirementTag.FIX_BUG:
                                  ["WriteCodePlanAndChange", "WriteCode", "WriteCodeReview",
                                   "SummarizeCode"]},
                           default_plan=["WriteCode", "WriteCodeReview", "SummarizeCode"],
                           watch={RequirementTag.WRITE_TASKS,
                                  RequirementTag.WRITE_CODE_PLAN_AND_CHANGE,
                                  RequirementTag.FIX_BUG, RequirementTag.DEBUG_ERROR}),
        "QA":        Agent({"name": "QA", "profile": "QA Engineer", "goal": "test the code"},
                           [WriteTest(llm=llm), RunCode(llm=llm), DebugError(llm=llm)], llm,
                           react_mode="REACT", max_loops=5,
                           watch={RequirementTag.SUMMARIZE_CODE}),
    }
