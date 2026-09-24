"""项目入口：源 team.py 的 run_project(:102) + run(:123) 合体。
run_project = 脚本场景的 async generator；prepare_project = runner 专用的三件套。"""
import re
from pydantic import BaseModel, Field
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
    ltm = kb = None
    if settings.enable_rag:                                 # 这个开关此前零读者，现在真管记忆召回
        from codeharness.memory.longterm import LongTermMemory
        from codeharness.provider.gateway import LLMGateway
        emb = LLMGateway.embeddings()                       # project 用时现取，三角色共用
        ltm = LongTermMemory(embeddings=emb)
        kb = LongTermMemory(embeddings=emb, doc_type="kb")  # C3：`UploadKB` 灌进去的那条切片的读者
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
    for a in agents.values():
        a.kb = kb                                 # 后挂而非塞进 ctor：位置参数已经排到第 12 个，别再排第 13 个
    sync_roster(agents)
    if TEAMLEADER_NAME in agents:
        # 源 TeamLeader._think(:66-67) 每轮重算 instruction=TL_INSTRUCTION；本仓走 instruction_provider
        from codeharness.prompts.di.team_leader import TL_INSTRUCTION
        leader = agents[TEAMLEADER_NAME]
        leader.instruction_provider = lambda: TL_INSTRUCTION.format(team_info=leader.team_info())
    return agents


def sync_roster(agents: dict) -> dict:
    """把「谁在队里」刷进每个 RoleZero 的 `teammates`（C1-②/③ 的共用出口）。

    两件事靠它：队长的 `{team_info}` 才有成员名可填（没有名册模型只能编名字），
    以及 `publish_team_message` 才有校验依据（在册才投）。**招人之后必须再调一次**，
    否则队长看不见新成员，委派永远到不了他。"""
    roster = {}
    for name, agent in agents.items():
        prof = getattr(agent, "profile", None) or {}
        roster[name] = f"{prof.get('profile', '')}, {prof.get('goal', '')}"
    for agent in agents.values():
        if hasattr(agent, "teammates"):
            agent.teammates = dict(roster)
    return roster


async def run_project(idea: str, project_id: str, agents: dict | None = None,
                      checkpointer=None, cost_manager=None):
    """async generator：产出 astream_events 事件（脚本场景）。runner 用 prepare_project。"""
    if agents is None:
        agents = _default_agents(cost_manager)
    from codeharness.environment.team_graph import build_team
    team = build_team(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}
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
            "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}
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


# ---------------- C1-③：现场招人（源无此能力，用户 2026-09-21 决策 #4 自建） ----------------
# 源只有装配期 `Team.hire(roles)`（metagpt/team.py:83），跑起来之后不再造人。
# 本仓按决策做「会话期内招人」：档案 = name/profile/goal/constraints + 从注册表选的工具名，
# 生效点是下一次起跑/续跑的装配（`runner._prepare`），不做图中热插——LangGraph 的节点集在
# compile 时就定了，热插只能整图重建 + 换 checkpointer 线程，代价远大于「下一跑生效」。

_NAME_OK = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")


class RoleDraft(BaseModel):
    """`draft_role_profile` 的输出契约（structured 强约束，与 ZeroThought 同一做法）。"""
    name: str = ""
    profile: str = ""
    goal: str = ""
    constraints: str = ""
    tools: list[str] = Field(default_factory=list)
    reason: str = ""


HIRE_SYSTEM = """You are a team composer for an autonomous software team. Design exactly ONE new team member.
Fields: name = one short capitalized English identifier; profile = job title; goal = what it must achieve;
constraints = rules it must obey; tools = ONLY names copied from the registry given below;
reason = why this member is needed. Keep name/profile/goal/constraints in the requirement's language
except the identifier `name`. Do not invent tools."""

HIRE_PROMPT = """# Requirement
{idea}

# Members already on the team
{teammates}

# Tool registry (copy these names verbatim into `tools`, or use none)
{tools}

Design the ONE member that the existing team cannot cover. Prefer few tools; prefer a narrow role."""


def check_role_def(defn: dict, taken=()) -> dict:
    """招人档案的**唯一**校验口（端点与装配都走它，别在第二处再写一遍规则）。

    不合法一律抛 ValueError（端点转 422），文本里带原因与可选项——「不合规」和「不知道为什么不合」
    是两件事，前者对用户没用。"""
    from codeharness.tools.tool_registry import TOOL_REGISTRY
    name = str(defn.get("name", "")).strip()
    if not _NAME_OK.match(name):
        raise ValueError("name 必须是字母开头、≤32 字符的标识符（不能含空格/中文/标点）——"
                         f"它同时是 LangGraph 的节点名，实收 {name!r}")
    if name in set(taken):
        raise ValueError(f"角色名 {name!r} 已在装配里，换一个或先删掉原成员")
    for field in ("profile", "goal"):
        if not str(defn.get(field, "")).strip():
            raise ValueError(f"{field} 不能为空（缺 profile/goal 的角色没有可判的行为）")
    known = [t.name for t in TOOL_REGISTRY.all()]
    tools = [str(t).strip() for t in (defn.get("tools") or []) if str(t).strip()]
    unknown = [t for t in tools if t not in known]
    if unknown:
        # 未注册的名字被静默丢掉 = 装配出来的角色不是请求的那个角色，比报错更糟
        raise ValueError(f"未注册的工具名 {unknown}（注册表共 {len(known)} 个，可选如 {known[:6]}…）")
    return {**defn, "name": name, "profile": str(defn["profile"]).strip(),
            "goal": str(defn["goal"]).strip(), "constraints": str(defn.get("constraints", "")).strip(),
            "tools": sorted(set(tools))}


def required_tier(tools: list[str]) -> str:
    """这批声明里最高的审批档。**不另立口径**：直接查执行期那张表（`_approval.TOOL_TIER`），
    表里没有的动作按 `full_access` 判——与那条表的 fail-closed 同一条规则。"""
    from codeharness.tools._approval import TIER_RANK, TOOL_TIER
    tiers = {TOOL_TIER.get(t, "full_access") for t in tools} or {"readonly"}
    return max(tiers, key=lambda t: TIER_RANK[t])


def build_hired_role(defn: dict, llm):
    """档案 → 一个可进图的 RoleZero。工具集走 `TOOL_REGISTRY.select`（名字已校验过）。

    空工具集是合法形态：纯思考型成员（评审、总结）不碰任何工具。"""
    from codeharness.configs.settings import settings
    from codeharness.roles.role_zero import RoleZero
    from codeharness.tools.tool_registry import TOOL_REGISTRY
    role = RoleZero({"name": defn["name"], "profile": defn.get("profile", ""),
                     "goal": defn.get("goal", ""), "constraints": defn.get("constraints", "")},
                    TOOL_REGISTRY.select(*defn.get("tools", [])), llm,
                    max_loops=int(defn.get("max_loops", 8)))
    # C24 行末那条余账：`_default_agents` 给队长与静态成员都挂了知识库读者（`team.py:36`），
    # 现场招进来的没有 ⇒ 同一场会话里「我上传的文档，队长查得到、我招的人查不到」，而界面上看不出来。
    # 租户与项目不传参，走 LongTermMemory 的 ContextVar 现取，与灌库侧、与队长那一份同源（C31 口径）。
    # **只挂 kb 不挂 ltm**：成员的角色记忆是 C1-③ 的另一笔账（那里连 ltm 都没有），不在这里顺手改。
    if settings.enable_rag:
        from codeharness.memory.longterm import LongTermMemory
        from codeharness.provider.gateway import LLMGateway
        role.kb = LongTermMemory(embeddings=LLMGateway.embeddings(), doc_type="kb")
    return role


async def draft_role_profile(llm, idea: str, teammates=None) -> dict:
    """让模型现场写一份档案（决策 #4 的「profile 现场写」）。**只回草案，不落库**——
    落库要走 `POST /roles`，那里才有档位审批与去重。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    from codeharness.tools.tool_registry import TOOL_REGISTRY
    names = [t.name for t in TOOL_REGISTRY.all()]
    prompt = HIRE_PROMPT.format(idea=idea[:2000], teammates=", ".join(teammates or []) or "(none)",
                                tools=", ".join(names))
    draft = await llm.structured(RoleDraft).ainvoke([SystemMessage(content=HIRE_SYSTEM),
                                                     HumanMessage(content=prompt)])
    out = draft.model_dump()
    unknown = [t for t in out["tools"] if t not in names]
    if unknown:
        raise ValueError(f"模型声明了未注册的工具 {unknown}，草案不作数（别把没登记的工具当成有）")
    return out
