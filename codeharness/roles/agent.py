"""单智能体 = role.py 语义的 LangGraph 化。
对照：_observe(:399)/_think(:340)/_act(:381)/_react(:458)/_get_prefix(:323) 逐行翻译见 docs/05。"""
from typing import TypedDict
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from codeharness.schema import Message


class ActionChoice(BaseModel):
    """REACT 模式的 LLM 决策（替代源 STATE_TEMPLATE 数字游戏，roles/prompt.py）"""
    thought: str
    action: str            # action 名或 "END"


class AgentState(TypedDict):
    name: str
    inbox: list            # RoleContext.msg_buffer 等价物
    memory: list           # RoleContext.memory 等价物
    action_cursor: int     # RoleContext.state 等价物（BY_ORDER）
    chosen: str            # REACT 的决策
    loops: int
    output: list


class Agent:
    def __init__(self, profile: dict, actions: list, llm, react_mode: str = "REACT",
                 max_loops: int = 3, watch: set | None = None, env_desc: str = ""):
        self.profile = profile
        self.actions = {a.name: a for a in actions}
        self.llm = llm
        self.react_mode = react_mode
        self.max_loops = max_loops
        self.watch = watch or {"UserRequirement"}       # 对齐 _process_role_extra(:177) 默认订阅
        self.env_desc = env_desc
        prefix = self.build_prefix()
        for a in actions:                                # 对齐 _process_role_extra(:173)
            a.prefix = prefix

    def build_prefix(self) -> str:
        """逐字对齐源 _get_prefix(:323-338) 的三段模板（源模板在 roles/prompt.py 的 PREFIX/CONSTRAINT_TEMPLATE）"""
        p = self.profile
        prefix = f"You are a {p.get('profile', 'helper')}, named {p['name']}, your goal is {p.get('goal', '')}."
        if p.get("constraints"):
            prefix += f" The constraint is {p['constraints']}."
        if self.env_desc:
            prefix += f" You are in {self.env_desc}."
        return prefix

    def build(self):
        g = StateGraph(AgentState)
        g.add_node("observe", self._observe)
        g.add_node("think", self._think)
        g.add_node("act", self._act)
        g.set_entry_point("observe")
        g.add_edge("observe", "think")
        g.add_conditional_edges("think", self._route)
        g.add_edge("act", "think")
        return g.compile()

    # ---- 源 _observe(:399-431) 逐行翻译：过滤条件一字未改(:415) ----
    async def _observe(self, s: AgentState):
        news = [m for m in s["inbox"]
                if (m.cause_by in self.watch or s["name"] in m.send_to) and m not in s["memory"]]
        return {"inbox": news, "memory": s["memory"] + news}

    # ---- 源 _think(:340-379) 两模式 ----
    async def _think(self, s: AgentState):
        names = list(self.actions)
        if len(names) == 1:                              # 对齐 :342 单动作直选
            return {"chosen": names[0], "loops": s["loops"] + 1}
        if self.react_mode == "BY_ORDER":                # 对齐 :353-357
            cursor = s["action_cursor"] + 1
            chosen = names[cursor] if cursor < len(names) else "END"
            return {"chosen": chosen, "action_cursor": cursor, "loops": s["loops"] + 1}
        from codeharness.report import thought_block
        async with thought_block(role=self.profile["name"]) as rep:
            choice: ActionChoice = await self.llm.structured(ActionChoice).ainvoke(
                self.build_prefix()
                + "\n可选动作: " + ", ".join(names)
                + "\n最新消息:\n" + (s["inbox"][-1].content[:2000] if s["inbox"] else "")
                + "\n根据最新消息选择下一个动作；全部完成填 END。")
            await rep.content(choice.thought)
        return {"chosen": choice.action, "loops": s["loops"] + 1}

    def _route(self, s: AgentState):
        if s["loops"] >= self.max_loops or not s["inbox"]:
            return END                                   # 对齐 is_idle(:561) + max_react_loop
        if s["chosen"] == "END" or s["chosen"] not in self.actions:
            return END
        return "act"

    # ---- 源 _act(:381-397) 逐行翻译 ----
    async def _act(self, s: AgentState):
        action = self.actions[s["chosen"]]
        prompt = self._format_inbox(s["inbox"])
        result = await action.run(Message(content=prompt, role="user", cause_by=s["inbox"][-1].cause_by))
        if isinstance(result, Message):
            msg = result
        else:
            msg = Message(content=str(result), role="assistant")
        msg.cause_by = action.name                       # 对齐 :388 cause_by=todo
        msg.sent_from = self.profile["name"]             # 对齐 :389 sent_from=self
        cursor = s["action_cursor"] + 1 if self.react_mode == "BY_ORDER" else s["action_cursor"]
        return {"output": s["output"] + [msg], "memory": s["memory"] + [msg],
                "inbox": [], "action_cursor": cursor}

    def _format_inbox(self, inbox) -> str:
        return "\n\n".join(m.content for m in inbox)

    # ---- 嵌入团队图（第 6 步）：折成一个节点，私有记忆持久化在 TeamState.memories ----
    def as_node(self, name: str):
        graph = self.build()

        async def _run(state: dict):
            from codeharness.report import set_role
            set_role(name)                  # 内核报道事件的 role 字段（前端块头显示角色名）
            inbox = state.get("_inbox") or []
            mem = list(state.get("memories", {}).get(name, []))
            result = await graph.ainvoke({"name": name, "inbox": inbox, "memory": mem,
                                          "action_cursor": 0, "chosen": "", "loops": 0, "output": []})
            return {"messages": result["output"], "memories": {name: result["memory"]}}

        return name, _run
