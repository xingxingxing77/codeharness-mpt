"""团队编排：源 base_env.py + team.py 的 LangGraph 化。
publish_message(:176) → route()；run(:198) → 图执行；is_idle(:230) → route 无目标即 END；
hire(:83) → 组图；invest/_check_balance(:92/:98) → budget_guard；run(n_round)(:123) → recursion_limit；
serialize/deserialize(:59/:67) → checkpointer（一行不写）。"""
import operator
from typing import Annotated, TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from codeharness.const import MESSAGE_ROUTE_TO_SELF, RequirementTag
from codeharness.schema import Message


def merge_dicts(a: dict, b: dict) -> dict:
    """并行 Send 时 dict 合并 reducer（memories 按角色名合并，防覆盖）"""
    return {**a, **b}


class TeamState(TypedDict):
    messages: Annotated[list, operator.add]        # 全局黑板 = env.history
    memories: Annotated[dict, merge_dicts]         # 每角色私有记忆（checkpointer 持久化）
    docs: dict                                     # filename -> Document（产物仓）
    round: int
    budget_used: float
    debug_rounds: int                              # QA 修复回路上限（参考速查 §2）
    finished: bool


# ---- 真实 SOP 订阅表（= 各角色 _watch + 参考速查全图；经典线） ----
SOP = {
    RequirementTag.USER_REQUIREMENT:   ["PM"],
    RequirementTag.WRITE_PRD:          ["Architect"],
    RequirementTag.WRITE_DESIGN:       ["PMManager"],
    RequirementTag.WRITE_TASKS:        ["Engineer"],
    RequirementTag.SUMMARIZE_CODE:     ["QA"],
    RequirementTag.WRITE_CODE_PLAN_AND_CHANGE: ["Engineer"],
    RequirementTag.FIX_BUG:            ["Engineer"],
    RequirementTag.DEBUG_ERROR:        ["Engineer"],   # QA 修复后回 Engineer 确认
}


def make_route(sop: dict, agents: dict):
    """= 源 base_env.publish_message(:176) 的路由语义，做成工厂便于测试替换 SOP 表"""

    def route(state: TeamState):
        if state.get("finished"):
            return END
        if not state["messages"]:
            return END
        last = state["messages"][-1]

        # <self> 自投递（源 role.publish_message:437-443 + const.py:83）
        if MESSAGE_ROUTE_TO_SELF in last.send_to:
            return [Send(last.sent_from, {"_inbox": [last]})]

        targets = sop.get(last.cause_by, [])
        # ---- 运行中插话（= 源 runner.send_chat:46 + MGXEnv 直聊分支；前端 InputCard "追问"） ----
        from codeharness.runtime import CHAT_SINK
        sends = [Send(t, {"_inbox": [last]}) for t in targets]
        chat = CHAT_SINK.get()
        if chat:
            for content, send_to in chat.drain():
                recv = send_to or chat.default_target      # 空 = TeamLeader（源 :59 语义）
                if recv in agents:
                    sends.append(Send(recv, {"_inbox": [Message(
                        content=content, cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user")]}))
        if not sends:
            return END                                     # 无订阅者且无插话 = is_idle 散会
        if last.cause_by == RequirementTag.DEBUG_ERROR:
            state["debug_rounds"] = state.get("debug_rounds", 0) + 1
            if state["debug_rounds"] >= 3:                 # 源 qa_engineer 隐式 3 轮上限
                return END
        return sends

    return route


async def budget_guard(state: TeamState):
    """= 源 team._check_balance(:98)。挂在 START 后、router 前"""
    from codeharness.configs.settings import settings
    if state.get("budget_used", 0.0) >= settings.max_budget:
        return {"finished": True}
    return {}


def build_team(agents: dict, checkpointer=None, sop: dict | None = None, cost_manager=None):
    """= 源 team.hire(:83)。agents: {name: Agent|RoleZero}，均提供 as_node(name) 接口"""
    route = make_route(sop or SOP, agents)
    g = StateGraph(TeamState)
    g.add_node("budget_guard", budget_guard)

    async def router(state: TeamState):
        return {}                                     # 汇聚虚节点：所有产出流回这里再路由

    g.add_node("router", router)
    for name, agent in agents.items():
        node_name, node_fn = agent.as_node(name)
        g.add_node(node_name, node_fn)
        g.add_edge(node_name, "router")

    g.add_edge(START, "budget_guard")
    g.add_edge("budget_guard", "router")
    g.add_conditional_edges("router", route)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
