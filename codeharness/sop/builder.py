"""build_team_from_template：模板 → 可执行团队图（复用 build_team，不重造编排）。

这是 runner/脚本用 SOP 模板跑会话的唯一出口：给 llm 与模板名，拿 (team, config, init)——
与 team.prepare_project 同构，interrupt/resume 语义因此一致。"""
from codeharness.sop.templates import builtin_templates


def get_template(name: str):
    tpls = builtin_templates()
    if name not in tpls:
        raise KeyError(f"未知 SOP 模板 {name!r}，可选: {sorted(tpls)}")
    return tpls[name]


def build_team_from_template(name: str, llm, *, checkpointer=None, extra_agents: dict | None = None,
                             idea: str = "", thread_id: str = ""):
    """返回 (team, config, init)：init 走 UserRequirement 消息，与 prepare_project 同出口。

    `idea` 缺省为空串（t19 平台验收线只验装配不跑会话）；server 的 runner 路径必须传，
    否则需求进不了图——Content 空的 UserRequirement 谁也没得干。
    `thread_id` 缺省 `sop:{name}`（脚本单场够用）；server 多会话共用模板时**必须**传
    会话唯一值，否则 checkpointer 按线程串台。"""
    from codeharness.const import RequirementTag
    from codeharness.environment.team_graph import build_team
    from codeharness.schema import Message

    tpl = get_template(name)
    agents = tpl.build_agents(llm)
    if extra_agents:
        agents.update(extra_agents)
    team = build_team(agents, checkpointer=checkpointer, sop=tpl.edges)
    config = {"configurable": {"thread_id": thread_id or f"sop:{name}"}, "recursion_limit": 60}
    init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "docs": {}, "round": 0, "debug_rounds": 0, "finished": False}
    return team, config, init
