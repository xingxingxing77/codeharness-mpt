"""单智能体 = role.py 语义的 LangGraph 化。
对照：_observe(:399)/_think(:340)/_act(:381)/_react(:458)/_get_prefix(:323) 逐行翻译见 docs/05。"""
from typing import TypedDict
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from codeharness.const import MESSAGE_ROUTE_TO_SELF
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
    plan: list             # 本次激活按触发源选定的动作序（源 Engineer._new_code_actions 的按因装配）
    loops: int
    output: list


class Agent:
    def __init__(self, profile: dict, actions: list, llm, react_mode: str = "REACT",
                 max_loops: int = 3, watch: set | None = None, env_desc: str = "",
                 plans: dict | None = None, default_plan: list | None = None):
        self.profile = profile
        self.actions = {a.name: a for a in actions}
        # 按触发源装配动作序（源 engineer._new_code_actions:328-428 的浓缩：FIX_BUG 先产计划）。
        # 键=触发消息 cause_by，值=动作名序；default_plan 缺省取全量动作表。
        # 装配名必须都在 actions 里——构造即校验，两张表漂移在实例化时就炸（三表互洽教训）。
        self.plans = plans or {}
        for tag, names in self.plans.items():
            unknown = [n for n in names if n not in self.actions]
            assert not unknown, f"plans[{tag}] 引用了未装配的动作: {unknown}"
        self.default_plan = default_plan or list(self.actions)
        unknown = [n for n in self.default_plan if n not in self.actions]
        assert not unknown, f"default_plan 引用了未装配的动作: {unknown}"
        self.llm = llm
        self.react_mode = react_mode
        # N3（施工3 批4）：执行策略是 profile 的一个字段，不是三套类——
        # BY_ORDER=顺序流水线（sop），REACT=按需挑 Action。源三套类收成一个运行时值。
        profile.setdefault("strategy", "sop" if react_mode == "BY_ORDER" else "react")
        self.max_loops = max_loops
        self.watch = watch or {"UserRequirement"}       # 对齐 _process_role_extra(:177) 默认订阅
        self.env_desc = env_desc
        self.kb = None                                  # C3 行②那半账：经典线的知识库读者，装配期挂
        prefix = self.build_prefix()
        for a in actions:                                # 对齐 _process_role_extra(:173)
            a.prefix = prefix

    def build_prefix(self) -> str:
        """逐字对齐源 _get_prefix(:323-338) 的三段模板（源模板在 roles/prompt.py 的 PREFIX/CONSTRAINT_TEMPLATE）"""
        p = self.profile
        prefix = f"You are a {p.get('profile', 'helper')}, named {p['name']}, your goal is {p.get('goal', '')}."
        if p.get("constraints"):
            prefix += f" The constraint is {p['constraints']}."
        if p.get("desc"):                                  # 源 Role.desc：对话型角色的人设原则全文
            prefix += f" {p['desc']}"
        if self.env_desc:
            prefix += f" You are in {self.env_desc}."
        return prefix

    def build(self):
        g = StateGraph(AgentState)
        g.add_node("observe", self._observe)
        g.add_node("think", self._think)
        g.add_node("gate", self._gate_action)          # 工具审批闸门（批次36）：无 LLM、无副作用
        g.add_node("act", self._act)
        g.set_entry_point("observe")
        g.add_edge("observe", "think")
        g.add_conditional_edges("think", self._route)
        g.add_edge("gate", "act")
        g.add_edge("act", "think")
        return g.compile()

    # ---- 工具审批闸门（S11-批次36）----
    def _approval_key(self, s: AgentState) -> tuple[str, dict]:
        """审批判定的唯一输入：Action 类名 + 能从 state 确定性复现的载荷。
        gate 与 act 两处必须算出同一个 approval_id，所以这段只许有一个出口。

        S4：`instruct_content` 必须在内——它就是下面 `_act` 原样透传给 Action 的那份载荷。
        不含它时，同一条触发消息下 command/working_directory 完全不同的两次 RunCode 算出
        **同一个 approval_id**：人工批一次 = 此后所有命令都被放行，批准粒度失真成「批消息」。
        放整包 dict 而不是截断后的 JSON 串：摘要只进 sha1，不心疼长度，而截断会把
        「差异在 cut 之后」的两笔又并成同一个 id——正是要修的洞。
        """
        action = self.actions.get(s["chosen"])
        name = type(action).__name__ if action is not None else str(s["chosen"])
        trig = (s["inbox"][-1] if s.get("inbox")
                else (s.get("memory") or [None])[-1])
        args = {"cause_by": str(getattr(trig, "cause_by", "") or ""),
                "send_from": str(getattr(trig, "send_from", "") or ""),
                "content": (getattr(trig, "content", "") or "")[:500],
                "instruct": getattr(trig, "instruct_content", None) or {}}
        return name, args

    async def _gate_action(self, s: AgentState):
        """经典线一个节点只跑一个 Action，粒度天然是「一次批一个动作」。

        与 role_zero 的 gate 同一套：结论只认台账，`interrupt()` 的返回值当唤醒信号。
        没装待批通道（内核直跑图）就跳过，保持原行为。"""
        from codeharness.runtime import APPROVAL_IO
        from codeharness.tools._approval import gate_decide
        io_ = APPROVAL_IO.get()
        if io_ is None or s["chosen"] not in self.actions:
            return {}
        name, args = self._approval_key(s)
        reason = f"{self.profile.get('name', '')} 要执行 {name}：{args['content'][:120]}"
        decision, item = gate_decide(name, args, node="gate", io_=io_, kind="action", reason=reason)
        while decision is None:
            io_.request(item)
            from langgraph.types import interrupt
            interrupt({"approval": item})
            decision, item = gate_decide(name, args, node="gate", io_=io_, kind="action",
                                         reason=reason)
        return {}

    # ---- 源 _observe(:399-431) 逐行翻译：过滤条件一字未改(:415) ----
    async def _observe(self, s: AgentState):
        news = [m for m in s["inbox"]
                if (m.cause_by in self.watch or s["name"] in m.send_to) and m not in s["memory"]]
        out = {"inbox": news, "memory": s["memory"] + news}
        if news:
            # 有新闻=新激活：按触发源选动作序；无新闻（续跑）保留本轮已定的 plan（游标走的是同一条序）
            out["plan"] = self.plans.get(news[-1].cause_by) or self.default_plan
        return out

    # ---- 源 _think(:340-379) 两模式（全部包 thought_block，前端每个思考步都有 Thought 块） ----
    async def _think(self, s: AgentState):
        from codeharness.report import thought_block
        names = s.get("plan") or list(self.actions)   # 本次激活的动作序（按因装配，缺省全量）
        if len(names) == 1 and self.react_mode == "REACT":   # 对齐 :342 单动作直选（仅 REACT；
            async with thought_block(role=self.profile["name"]) as rep:   #  BY_ORDER 必须走 cursor 才能止于 len）
                await rep.content(f"[单动作] 直接执行 {names[0]}")
            return {"chosen": names[0], "loops": s["loops"] + 1}
        if self.react_mode == "BY_ORDER":                # 对齐 :353-357（源 state 从 -1 起步，think 先进位）
            cursor = s["action_cursor"] + 1
            if cursor >= len(names):
                return {"chosen": "END", "action_cursor": cursor, "loops": s["loops"] + 1}
            async with thought_block(role=self.profile["name"]) as rep:
                await rep.content(f"[计划] 执行 {names[cursor]}")
            return {"chosen": names[cursor], "action_cursor": cursor, "loops": s["loops"] + 1}
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
        # 对齐源 _react(:458) 循环条件：只有 max_react_loop 与"无 todo"两个停止条件
        # （inbox 为空不能停——BY_ORDER 的第二个动作仍要跑；散会语义在团队层 route）
        if s["loops"] >= self.max_loops:
            return END
        if s["chosen"] == "END" or s["chosen"] not in self.actions:
            return END
        return "gate"

    # ---- 源 _act(:381-397) 逐行翻译 + self-heal 收口 ----
    async def _kb_block(self, task: str) -> str:
        """这一轮动作的知识库预取。补的是 C3 行②留的那半截账：dynamic 线从 C3 起就有读者，
        classic/react 线没有 ⇒ 用户上传了文档、跑默认 paradigm 却一条都引用不到，而界面上不报错。

        与 `RoleZero._kb_recall` 同一档位：检索链任何一环挂了都按「没有资料」继续，
        不为一次召回打断整场（那会把已完成的工作与已花的钱一起陪葬，第十二处教训）。
        """
        from codeharness.configs.settings import settings
        if self.kb is None or not settings.enable_rag or not task.strip():
            return ""
        try:
            from codeharness.memory.longterm import format_kb_blocks
            return format_kb_blocks(await self.kb.recall(task, k=3))
        except Exception as e:
            from codeharness.logs import logger
            logger.warning(f"{self.profile['name']} 知识库召回失败，按无资料继续: "
                           f"{type(e).__name__}: {e}")
            return ""

    async def _act(self, s: AgentState):
        from langgraph.errors import GraphInterrupt
        action = self.actions[s["chosen"]]
        # 执行前读台账（批次36）：被拒的动作**不 run**，回一条拒绝消息进记忆，走 [错误] 那条自愈路径，
        # 而不是把会话炸掉。判定输入与 gate 共用 _approval_key，两边算出的 approval_id 必须一致。
        from codeharness.runtime import APPROVAL_IO
        from codeharness.tools._approval import gate_decide
        io_ = APPROVAL_IO.get()
        if io_ is not None:
            name, args = self._approval_key(s)
            verdict, _ = gate_decide(name, args, node="gate", io_=io_, kind="action")
            if verdict != "allowed":
                # 未决（None）也按不执行处理：gate 漏接线的后果必须是「没干」，不是「照干」
                msg = Message(content=f"[已拒绝] {name} 未获批准，不执行",
                              role="user", cause_by=name, sent_from=self.profile["name"],
                              send_to={MESSAGE_ROUTE_TO_SELF})
                return {"output": s["output"] + [msg], "memory": s["memory"] + [msg],
                        "inbox": [], "action_cursor": s["action_cursor"]}
        if s["inbox"]:                                       # 首个动作：触发源 = 最新收件
            prompt = self._format_inbox(s["inbox"])
            trig = s["inbox"][-1]
        else:                                                # BY_ORDER 第 2+ 动作：收件箱已清，退化为最近记忆
            trig = s["memory"][-1] if s["memory"] else Message(content="")
            prompt = trig.content
        # 注：跨动作上下文走 trig.instruct_content 透传（下面 run(...)），不改 msg.content——
        # content 是下一动作的工作载荷（如 RunPythonCode 直接把它当代码执行），前缀散文会污染。
        # 「经典线记忆回喂进 prompt」是 _think 侧 system 上下文的事（对照1 §五-8），不在 _act 做。
        # 知识库那条不一样：动作的 prompt 在 `Action._ask` 那个唯一出口上才成型，所以在这里取一次、
        # 经 ContextVar 交给它（`runtime.KB_CONTEXT`）——**一次动作只检索一次**，补问轮复用同一份。
        from codeharness.runtime import KB_CONTEXT
        kb_tok = KB_CONTEXT.set(await self._kb_block(prompt))
        try:
            result = await action.run(Message(
                content=prompt, role="user", cause_by=trig.cause_by, sent_from=trig.sent_from,
                instruct_content=trig.instruct_content,      # 上下文模型透传（CodingContext/TestingContext 的接缝）
                instruct_schema=trig.instruct_schema))
        except GraphInterrupt:
            raise                                            # interrupt 靠抛异常暂停图，绝不能吞（role_zero 同律）
        except Exception as e:
            # Action 抛错 → 错误消息回喂记忆，下一轮自愈——真模型输出漂移是常态（第十二处：
            # WriteTasks 给了 filename="/main.py"，产物仓按契约写拒，这异常原本一路吹穿
            # team graph，把已完成的角色与花掉的钱全部陪葬）。拒写是对的，炸会话不是。
            # 「下一轮」靠 `<self>` 让团队图把本角色再激活一次（B9）：cause_by=action.name 不在
            # SOP 表里，而默认 send_to=<all> 在 route 里刻意不广播 → 无订阅者 = 一抛错就散会。
            # ⚠ 自愈可见度只到 BY_ORDER：REACT 的 _think 只读 inbox[-1]，而这条消息过不了
            # _observe 的 watch 过滤（cause_by=action.name 不在 watch、send_to=<self> 不含角色名）
            # → REACT 下一轮看不到错误文本。更深的设计缝隙，B9 不扩 scope（总文档 §B9 风险②）。
            from codeharness.logs import logger
            logger.warning(f"{self.profile['name']}.{action.name} 抛错，回喂自愈: "
                           f"{type(e).__name__}: {e}")
            result = Message(content=f"[错误] {action.name} 执行失败: {type(e).__name__}: {e}",
                             role="user", cause_by=action.name, sent_from=self.profile["name"],
                             send_to={MESSAGE_ROUTE_TO_SELF})
        finally:
            # 挂在 try 上而不是 try 后：GraphInterrupt 那条要暂停整场、抛错那条要自愈，
            # 两支都得把这份上下文摘掉——留着下一轮就会拿上一轮动作的知识库片段干活。
            KB_CONTEXT.reset(kb_tok)
        if isinstance(result, Message):
            msg = result
        else:
            msg = Message(content=str(result), role="assistant")
        if not msg.cause_by or msg.cause_by == trig.cause_by:
            msg.cause_by = action.name                   # 对齐 :388 cause_by=todo（Action 未显式设 tag 时）
        msg.sent_from = self.profile["name"]             # 对齐 :389 sent_from=self
        return {"output": s["output"] + [msg], "memory": s["memory"] + [msg],
                "inbox": [], "action_cursor": s["action_cursor"]}   # BY_ORDER 的进位已在 think 完成

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
                                          "action_cursor": -1, "chosen": "", "loops": 0, "output": []})
            return {"messages": result["output"], "memories": {name: result["memory"]}}

        return name, _run
