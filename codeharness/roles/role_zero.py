"""RoleZero：动态范式（源 roles/di/role_zero.py:55-475 的语义 → 图）。
_think(:198) 八步组装 → think 节点；_act(:280) 命令执行 → act 节点；
ask_human(:456)/reply_to_human(:465)/_end(:474) → interrupt/记录/END。
工作记忆照源 :241 `memory.get(memory_k)` 窗口回喂，溢出交 BrainMemory 摘要（S5.1）；
经验池 `@exp_cache` 接在 `llm_cached_think`（S5.3，对应源 :267 `llm_cached_aask`——包的是
纯问函数，命中=跳过模型；默认全关，`EXP_POOL__ENABLED/ENABLE_READ/ENABLE_WRITE` 开）。"""
import asyncio
import json
from datetime import datetime
from typing import TypedDict
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from codeharness.configs.settings import settings
from codeharness.const import RequirementTag
from codeharness.exp_pool import exp_cache
from codeharness.exp_pool.serializers import RoleZeroSerializer
from codeharness.logs import logger
from codeharness.memory.brain_memory import BrainMemory
from codeharness.memory.memory import Memory
from codeharness.schema import Message, Command, Plan
from codeharness.prompts.role_zero import (SYSTEM_PROMPT, CMD_PROMPT, ROLE_INSTRUCTION,
                                           TASK_TYPE_DESC)
from codeharness.tools.tool_recall import select_for_prompt


class ZeroThought(BaseModel):
    """输出契约：structured 强约束（替代源 parse_commands + JSON_REPAIR 重试）"""
    thought: str
    commands: list[Command] = Field(default_factory=list)


class RoleZeroState(TypedDict):
    task: str
    history: list          # [{thought, commands:[{command_name,args}], results:[{name,result}]}]
    experience: str
    respond_language: str
    finished: bool
    # C59：`_act` 执行到第几条命令的**续跑游标**（0 = 从头）。中断恢复时 LangGraph 从节点开头重跑
    # 整个节点函数，没有游标就只能把中断前的命令再跑一遍（真图实测副作用 1→2 次）。
    # `_think` 每产出一个新命令列表就把它重置为 0——忘了这条，新一列命令会被旧游标跳过头几条。
    act_cursor: int
    # C59：`_act` **因为碰到 ask_human 而停下**（命令没跑完，等人回话）时为 True。
    # 它是 `act → ?` 那条条件边的判据：`True` 走零副作用的 `ask` 节点，否则才回 `think`。
    # ⚠ 不能改用「`_act` 返回 `Command(goto="ask")`」——静态条件边不会被 goto 压掉，两个目标会
    # **同时被调度**（实测：`think` 在没人回话时就跑了，resume 时两个节点挤进同一 tick 各写一次
    # `history` ⇒ `InvalidUpdateError`）。路由必须由这一条条件边**唯一**决定。
    pending_ask: bool


class RoleZero:
    def __init__(self, profile: dict, tools: list, llm, system_prompt: str = SYSTEM_PROMPT,
                 instruction: str = ROLE_INSTRUCTION, max_loops: int = 15, env_desc: str = "",
                 longterm_memory=None, memory: Memory | None = None,
                 brain: BrainMemory | None = None, redis_key: str = "", memory_k: int = 0,
                 plan_fn=None, instruction_provider=None, task_type_desc: str = None, example: str = ""):
        self.profile = profile
        profile.setdefault("strategy", "role_zero")   # N3：RoleZero 引擎的自报策略（见施工3 批4）
        self.tools = {t.name: t for t in tools}
        self.llm = llm
        self.system_prompt = system_prompt
        self.instruction = instruction
        self.instruction_provider = instruction_provider   # 批次3：源 TL 每轮 _think 重算 instruction 的等价钩子
        self.task_type_desc = task_type_desc               # 批次3：源 RoleZero.task_type_desc 类字段的构造期等价
        self.example = example                             # 批次3：源 _retrieve_experience 的静态覆写口（如 ARCHITECT_EXAMPLE）
        self.max_loops = max_loops
        self.env_desc = env_desc
        self.plan_fn = plan_fn              # 批次1：ToT 等外部规划器接此处；as_node 新任务时先规划再 think
        self.ltm = longterm_memory          # 第 4 步 LongTermMemory，可空
        self.kb = None                      # C3：知识库读者（doc_type="kb"），装配期挂；None=没订阅
        self.memory = memory if memory is not None else Memory()
        self.brain = brain                  # None → 超窗直接丢，语义同源的 memory_k 截断
        self.redis_key = redis_key
        self.memory_k = memory_k or settings.memory_overflow_size
        self._brain_loaded = False
        # 计划状态机（接线台账 #10）：schema.Plan 从此有运行时读者——源 tool_execution_map
        # :121-124 的 Plan.* 四命令吃它。随会话实例存活；跨进程重启丢计划（与子图 InMemorySaver
        # 同一层已知债，S7 落盘时一起收）。
        self.plan: Plan | None = None
        self._plan_goal = ""
        # 委派名册（C1-②）：{成员名: "profile, goal"}。由装配方（team.default_team）在组队后填，
        # 空名册=这角色没进过队，publish_team_message 直接拒（否则 route 的 UnknownRecipient
        # 会把整场已烧的用量陪葬——模型写错名字是常态，不是编程错误）。
        self.teammates: dict[str, str] = {}
        self._outbox: list[tuple[str, str]] = []    # [(任务指令, 成员名)]，as_node 收口时发成 Send
        self._report_to = ""                        # 委派我的人，干完就向他回报（C1-②b）

    PLAN_COMMANDS = {   # 源 :121-124 的 Plan 命令面
        "Plan.append_task": "append_task", "Plan.reset_task": "reset_task",
        "Plan.replace_task": "replace_task", "Plan.finish_current_task": "finish_current_task",
    }
    # 源 TeamLeader._update_tool_execution(:41-47)：真名 + publish_message 别名，两个键同一个实现
    PUBLISH_COMMANDS = {"TeamLeader.publish_team_message", "TeamLeader.publish_message"}

    def team_info(self) -> str:
        """源 TeamLeader._get_team_info(:50-57)：`名字: profile, goal` 一行一个。
        模型照这份名册填 `send_to`，名字对不上才有的可纠。"""
        return "".join(f"{n}: {desc}\n" for n, desc in sorted(self.teammates.items()))

    def _publish_team_message(self, args: dict) -> str:
        """源 team_leader.py:81-91：把任务交给某个成员，成员就此开工。

        源在命令里直接 `env.publish_message`；本仓的投递边界是团队图的 `route()`，
        所以这里只把 (指令, 成员) 记进 outbox，由 `as_node` 在节点收口时发成消息。"""
        if not self.teammates:
            return "[已忽略] 本角色不在任何团队里（没有队友名册），无法委派"
        raw = args.get("send_to", "")
        members = raw if isinstance(raw, (list, tuple, set)) else [raw]
        unknown = [str(m) for m in members if str(m) not in self.teammates]
        if unknown:
            # 当场拒不当场抛：这条文本经 _observe 回喂下一轮，模型自己改名字重发（B9 同一条自愈路）
            return f"[已拒绝] 成员 {unknown} 不在团队（在册: {sorted(self.teammates)}）"
        content = str(args.get("content", "") or "")
        self._outbox.extend((content, str(m)) for m in members)
        return f"[已委派] → {', '.join(str(m) for m in members)}"

    def _run_plan_command(self, name: str, args: dict) -> str:
        """真身 schema.Plan：拓扑排序/级联 reset/游标推进都在里面。参数错（缺 task_id、
        未知依赖断言）照源不兜——抛出去由 _act 的 [错误] self-heal 回喂模型重发。"""
        if self.plan is None:
            self.plan = Plan(goal=self._plan_goal)
        fn = getattr(self.plan, self.PLAN_COMMANDS[name])
        fn(**args)
        cur = self.plan.current_task
        where = f"{cur.task_id}: {cur.instruction[:60]}" if cur else "(全部完成)"
        if name == "Plan.finish_current_task":
            return f"[plan] 已推进 → {where}; 计划完成: {self.plan.is_plan_finished()}"
        return f"[plan] 共 {len(self.plan.tasks)} 任务, 当前 → {where}"

    def _brain_key(self) -> str:
        """一个角色一个 key：目录名走 CURRENT_PROJECT（与 session_root 同一接缝），不另起一套会话对象。"""
        if self.redis_key:
            return self.redis_key
        from codeharness.runtime import CURRENT_PROJECT
        from codeharness.const import BRAIN_MEMORY
        return BrainMemory.to_redis_key(BRAIN_MEMORY, "default", f"{CURRENT_PROJECT.get()}/{self.profile['name']}")

    # ---- 工作记忆（源 roles/di/role_zero.py:241 memory.get(memory_k) + :287-295 回喂） ----
    def _observe(self, s: RoleZeroState):
        """把上一轮 _act 的结果收进记忆。多命令轮里 results 会短于 commands，按内容去重防重复入库。"""
        if not s["history"]:
            return
        last = s["history"][-1]
        for r in last.get("results") or []:
            text = f"{r['name']}: {r['result']}"
            if not self.memory.try_remember(text):
                self.memory.add(Message(content=text, role="user",
                                        cause_by=RequirementTag.RUN_COMMAND,
                                        sent_from=self.profile["name"]))

    async def _compress(self):
        """超窗：窗口外那截按源的分工两路走——逐字引用进 Qdrant(ltm)，背景理解进摘要(brain)。"""
        if len(self.memory.storage) <= self.memory_k or (self.brain is None and self.ltm is None):
            return
        evicted = self.memory.storage[:-self.memory_k]
        self.memory.storage = self.memory.storage[-self.memory_k:]
        if self.ltm is not None:
            try:
                await self.ltm.overflow(evicted)
            except Exception as e:                    # embedding/Qdrant 不可用只降级，不能把角色跑死
                logger.warning(f"{self.profile['name']} 长期记忆入库失败，本批只走摘要: "
                               f"{type(e).__name__}: {e}")
        if self.brain is None:
            return
        for m in evicted:
            self.brain.add_history(m)
        try:
            await self.brain.summarize(self.llm, redis_key=self._brain_key())
        except ValueError as e:                     # 摘要没产出不能把记忆丢了——退回原样，下轮再试
            logger.warning(f"{self.profile['name']} 记忆压缩失败，保留窗口外 {len(evicted)} 条不裁剪: {e}")
            self.memory.storage = evicted + self.memory.storage

    async def _ltm_recall(self, task: str) -> str:
        """新任务先召回同项目的历史（源 _retrieve_experience:449 的位置）。"""
        try:
            return "\n".join(m.content for m in await self.ltm.recall(task, k=3))
        except Exception as e:
            logger.warning(f"{self.profile['name']} 长期记忆召回失败，按无经验继续: "
                           f"{type(e).__name__}: {e}")
            return ""

    async def _kb_recall(self, task: str) -> str:
        """知识库切片召回（C3 的下半截：`UploadKB` 灌进去的东西得有读者，否则写进去就是死数据）。
        与 `_ltm_recall` 同一档位：检索链任何一环挂了都按「没有资料」继续，不为一次召回打断这场。

        C22：每条切片单独挂一行**可 grep 的来源标记**（`.pdf` 带页码）。C21 只把出处接到了数据面
        （`Message.metadata`），而模型读的是这段文本——没有标记时它答完没法归因、用户也没法拿它去核。
        排版现在住在 `longterm.format_kb_blocks`：C24 那件模型可主动调的检索工具用同一个函数。
        """
        try:
            from codeharness.memory.longterm import format_kb_blocks
            return format_kb_blocks(await self.kb.recall(task, k=3))
        except Exception as e:
            logger.warning(f"{self.profile['name']} 知识库召回失败，按无资料继续: "
                           f"{type(e).__name__}: {e}")
            return ""

    def _context_messages(self) -> list:
        out = []
        if self.brain is not None and self.brain.historical_summary:
            out.append(SystemMessage(content=f"[历史摘要] {self.brain.historical_summary}"))
        for m in self.memory.get(self.memory_k):
            out.append(AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content))
        return out

    # ---- 源 _get_prefix(:276)：人设 + 约束 + 当前时间（对齐 Agent.build_prefix 的三段式）----
    def _prefix(self) -> str:
        p = self.profile
        s = (f"You are a {p.get('profile', 'helper')}, named {p['name']}, "
             f"your goal is {p.get('goal', '')}.")
        if p.get("constraints"):
            s += f" The constraint is {p['constraints']}."
        if p.get("desc"):
            s += f" {p['desc']}"
        if self.env_desc:
            s += f" You are in {self.env_desc}."
        s += f" The current time is {datetime.now():%Y-%m-%d %H:%M:%S}."
        return s

    def _plan_status(self, s: RoleZeroState):
        """源 :216 get_plan_status。B4 起计划从**真 Plan 状态机**出（对勾=Task.is_finished、
        游标=current_task，且 get_plan_status 走 _update_current_task 的拓扑序）；
        无计划时（首轮或未用过 Plan.* 的任务型角色）退化为 history 的 thought 摘要。"""
        if self.plan and self.plan.tasks:
            lines = [f"- [{'x' if t.is_finished else ' '}] {t.task_id}: {t.instruction[:80]}"
                     + (f" (assignee: {t.assignee})" if t.assignee else "")
                     for t in self.plan.tasks]
            cur = self.plan.current_task
            return "\n".join(lines), f"{cur.task_id}: {cur.instruction[:120]}" if cur else "(all finished)"
        lines = [f"- [{i+1}] {h['thought'][:80]}" for i, h in enumerate(s["history"][-5:])]
        return "\n".join(lines) or "(no plan yet)", f"step {len(s['history'])+1}/{self.max_loops}"

    def build(self, checkpointer=None):
        g = StateGraph(RoleZeroState)
        g.add_node("think", self._think)
        g.add_node("gate", self._gate_commands)        # 工具审批闸门（批次36）：无 LLM、无副作用
        g.add_node("act", self._act)
        # C59：`ask_human` 单独一个节点（**零副作用**，所以恢复时重放它无害）。为什么必须有它：
        # `interrupt()` 恢复时 LangGraph 从**节点开头**重跑节点函数，而 `_act` 只在函数末尾才返回
        # 状态更新 ⇒ 中断前已跑完的命令会**再跑一遍**（真图实测 `write_file` 调用 1→2 次）。
        # 这与 `gate` 是同一范式，纪律写在 `strategy/plan_and_act.py:6-7`。
        g.add_node("ask", self._ask)
        g.set_entry_point("think")
        g.add_conditional_edges("think", lambda s: END if s["finished"] else "gate")
        g.add_edge("gate", "act")

        def _after_act(s: RoleZeroState):
            """C59：`act` 的下一步**由这一条条件边唯一决定**（别用 `Command(goto=)`，见 `pending_ask` 的注释）。

            顺序有讲究：`pending_ask` 排在 `finished` 之前——命令列表写成 `[end, ask_human]` 时
            （模型偶尔会这么写），旧行为是**照样把问题问出去**，这里要保住它：问完再由 `finished`
            收口。反过来的话，那条 ask 会被静默丢掉、人永远等不到问题。
            """
            if s.get("pending_ask"):
                return "ask"
            return END if s["finished"] else "think"

        g.add_conditional_edges("act", _after_act)
        # `ask` 之后**必须回 `gate`**，不能直接回 `act`：问完之后剩下的命令还没过审批闸门，
        # 直连 `act` 就等于让 ask 之后那半条命令绕过审批（这条也是判据之一）。
        g.add_edge("ask", "gate")
        return g.compile(checkpointer=checkpointer or InMemorySaver())

    # ---- 工具审批闸门（S11-批次36）----
    async def _gate_commands(self, s: RoleZeroState):
        """逐条判档，需要审批的**一条一问**（状态并行、UI 串行，照参考项目 `apply.ts:108-109`）。

        `interrupt()` 的返回值只当「有人回过话」的信号，结论一律回台账读——所以重放时台账已命中的
        条目不再 interrupt，中断序号与 langgraph 缓存对不对齐都不影响判定。
        没装待批通道（内核直跑图：门禁、离线测试）就直接跳过，保持原行为。"""
        from codeharness.runtime import APPROVAL_IO
        from codeharness.tools._approval import gate_decide
        io_ = APPROVAL_IO.get()
        if io_ is None:
            return {}
        last = s["history"][-1] if s["history"] else {}
        reason = str(last.get("thought", ""))[:200]
        # C59：只 police **还没跑的那一截**（游标之后）。第一趟游标是 0、等价于全列表（原行为）；
        # ask 回来后游标已前进，已批准并执行过的命令不该再去台账上问第二遍。
        start = int(s.get("act_cursor") or 0)
        for cmd in last.get("commands", [])[start:]:
            name, args = cmd["command_name"], cmd.get("args", {})
            # 只 police `self.tools` 里真有的工具（C15）：`end`/`RoleZero.*`/`Plan.*`/`publish_*`
            # 在 `_act` 里走特殊分支、一次副作用都不发生，而 `_approval.TOOL_TIER` 没登记它们，
            # fail-closed 就把它们判成 `full_access` 要批 —— A4 真跑实测 readonly 会话里
            # 队长每次收口都被问一次「批准 end？」，审批面被空操作刷满。
            if name not in self.tools:
                continue
            decision, item = gate_decide(name, args, node="gate", io_=io_, reason=reason)
            while decision is None:
                io_.request(item)                          # HSETNX：重放不会冒出第二张卡
                interrupt({"approval": item})
                decision, item = gate_decide(name, args, node="gate", io_=io_, reason=reason)
        return {}

    # ---- 源 llm_cached_aask(:267) 的对应件：带经验池缓存的单次 think ----
    @exp_cache(serializer=RoleZeroSerializer())
    async def llm_cached_think(self, *, req: list, **kw) -> str:
        """返回 ZeroThought 的 JSON 字符串：字符串过经验池无损 roundtrip（源的 cached_aask 同形），
        schema 校验留在调用侧。命中即整次 structured 不进模型——落账口径里这次调用 token 为零。"""
        thought: ZeroThought = await self.llm.structured(ZeroThought).ainvoke(req)
        return thought.model_dump_json()

    # ---- 源 _think(:198-265) 的组装顺序（2 检测语言并入首轮、5 工具清单、8 查重由 structured 取代） ----
    async def _think(self, s: RoleZeroState):
        if len(s["history"]) >= self.max_loops:
            return {"finished": True}
        if self.brain is not None and not self._brain_loaded:
            self._brain_loaded = True               # 只恢复一次：每轮都读 Redis 会把摘要盖回旧值
            self.brain = await self.brain.loads(self._brain_key())
        self._observe(s)                              # 源 :295：上一轮命令结果进记忆，否则下一轮看不见
        await self._compress()
        experience = s.get("experience", "")
        if self.ltm and not experience:                       # 源 :213 _retrieve_experience + 第 4 步 recall
            experience = await self._ltm_recall(s["task"])
        # 知识库单独一次召回、单独一条 system 消息：它不是「角色自己的经验」，混进 experience 槽
        # 会让 prompt 里「经验」两个字骗人（A4 那批就是靠 prompt 文本判读写路的）。
        kb = await self._kb_recall(s["task"]) if self.kb is not None else ""

        # C6：名册长过 `TOOL_RECALL__MIN_TOOLS` 才开始裁工具块（默认 30，今天 19 只 ⇒ 逐字不变）。
        # query 传本轮任务文本，不学源那样留 force 开关（源的 think 主路径因此从没跑过两级召回）。
        tool_block = await select_for_prompt(self.tools, s["task"], llm=self.llm)
        tool_info = json.dumps({n: {"description": t.description} for n, t in tool_block.items()},
                               ensure_ascii=False)
        plan_status, current_task = self._plan_status(s)
        # 批次3：instruction 可每轮重算（源 TL 把 team_info 现填）；task_type_desc/example 接构造期覆写。
        _instruction = self.instruction_provider() if self.instruction_provider else self.instruction
        system_prompt = self.system_prompt.format(
            role_info=self._prefix(), task_type_desc=self.task_type_desc or TASK_TYPE_DESC,
            available_commands=tool_info, example=(self.example or experience), instruction=_instruction)
        prompt = CMD_PROMPT.format(experience=experience, current_state="ready",
                                   plan_status=plan_status, current_task=current_task,
                                   respond_language=s.get("respond_language", "中文"))
        # Thought 块（第 10 步 §1.2）：前端"思考中"卡片；structured 无 token 流，整段上屏（打字机见文末备注）
        from codeharness.report import thought_block
        from codeharness.reflection import detect_repeated_error, reflect
        err = detect_repeated_error(s["history"])
        if err:                                               # 源 utils/reflection.py：重复失败 → 自反思
            experience = (experience + "\n" +
                          await reflect(self.llm, s["task"], s["history"], err)).strip()
        async with thought_block(role=self.profile["name"]) as rep:
            context = ([SystemMessage(content=system_prompt)]
                       + ([SystemMessage(content=f"[知识库片段]\n{kb}")] if kb else [])
                       + self._context_messages())
            try:
                thought = ZeroThought.model_validate_json(
                    await self.llm_cached_think(req=context + [HumanMessage(content=prompt)]))
            except Exception:
                # structured 失败 → 纯文本重问 + repair 管线 + LLM 自修（源 parse_commands + JSON_REPAIR 链）
                from codeharness.provider.repair import llm_repair_json
                raw = await self.llm.aask([*context, HumanMessage(content=prompt)], tag="rz_fallback")
                thought = await llm_repair_json(raw, ZeroThought, self.llm) or ZeroThought(
                    thought=f"[解析失败，已按 end 处理] {raw[:200]}",
                    commands=[{"command_name": "end", "args": {}}])
            await rep.content(thought.thought)
        commands = [c.model_dump() for c in thought.commands]
        if not commands:                                      # 契约：至少一条命令，否则视作结束
            commands = [{"command_name": "end", "args": {}}]
        self.memory.add(Message(content=thought.thought, role="assistant",
                                cause_by=RequirementTag.RUN_COMMAND, sent_from=self.profile["name"]))
        # C59：新命令列表配新游标。**必须在这里重置**——`_act` 的续跑游标是「这一列命令跑到第几条」，
        # 而 `_think` 每次都产出一列全新的命令；不重置，第二轮的 `_act` 会拿上一轮的游标跳过头几条。
        # （C13 那轮踩的是同族形状：投递游标 `seen` 刻意不进 init。）
        return {"history": s["history"] + [{"thought": thought.thought, "commands": commands}],
                "act_cursor": 0, "pending_ask": False}

    # ---- 源 _act(:280-301)/_run_commands(:385)/_run_special_command(:420) ----
    # config 参数由 LangGraph 注入；interrupt() 的 get_config 依赖它（langgraph 1.x 节点执行路径不自带）
    async def _act(self, s: RoleZeroState, config: RunnableConfig | None = None):
        """顺序执行本轮的 `commands`。**可重入**（C59）：从 `act_cursor` 处接着跑。

        为什么需要游标：`interrupt()` 恢复时 LangGraph 从**节点开头**重跑整个节点函数（它没有
        「从函数中间续跑」的能力），而本函数只在**末尾**返回状态更新 ⇒ 中断前跑完的那截命令，
        结果一个字都没落进 state，重放时全部再跑一遍。真图实测：一条 `[write_file, ask_human]`
        的命令列表，`ask` 之前 `write_file` 调 1 次、resume 之后调 **2** 次。
        现在碰到 `ask_human` 就**不在这里 interrupt**，而是带着「跑到第几条」交给零副作用的 `ask` 节点。

        ⚠ 已跑完的那截结果从 `last["results"]` 续上（`ask` 节点写它就是为这个），别再从头 append。
        """
        from langchain_core.runnables.config import var_child_runnable_config
        tok = var_child_runnable_config.set(config)
        try:
            last = s["history"][-1]
            commands = last["commands"]
            start = int(s.get("act_cursor") or 0)          # 0 = 这一列命令从头跑
            results, finished = list(last.get("results") or []), False
            pending_at = -1                                # >=0 = 停在这里等人回话
            for idx in range(start, len(commands)):
                cmd = commands[idx]
                name, args = cmd["command_name"], cmd.get("args", {})
                if name == "RoleZero.ask_human":           # 源 ask_human(:456) → 交 `ask` 节点
                    # C59：**在这里不 interrupt**，只记下「停在第几条」交给 `ask`（零副作用节点）。
                    # 恢复时重放的是 `ask`，上面那些副作用一次都不会重来。
                    pending_at = idx
                    break
                try:
                    if name in ("end", "End"):                    # 源 _end(:474)
                        finished = True
                        results.append({"name": name, "result": "[结束]"})
                    elif name == "RoleZero.reply_to_human":       # 源 reply_to_human(:465)
                        results.append({"name": name, "result": f"[已回复] {args.get('content', '')}"})
                    elif name in self.PUBLISH_COMMANDS:           # 源 TeamLeader.publish_team_message(:81)
                        results.append({"name": name, "result": self._publish_team_message(args)})
                    elif name in self.PLAN_COMMANDS:            # 台账 #10：真 Plan 状态机（源 :121-124）
                        results.append({"name": name, "result": self._run_plan_command(name, args)})
                    elif name in self.tools:
                        # 执行前再判一次档：gate 节点负责挂起问人，这里只读台账结论——
                        # 被拒的命令**不进 ainvoke**，副作用一次都不发生。
                        from codeharness.runtime import APPROVAL_IO
                        from codeharness.tools._approval import gate_decide
                        io_ = APPROVAL_IO.get()
                        verdict, _ = gate_decide(name, args, node="gate", io_=io_,
                                                 reason=str(last.get("thought", ""))[:200])
                        if verdict != "allowed":
                            # 未决（None）也按不执行处理：正常图里 gate 一定先跑，走到 act 还
                            # 没有结论只可能是接线漏了——这种情况绝不默默放行副作用。
                            results.append({"name": name, "result": "[已拒绝] 未获批准，不执行"})
                            # 被拒也要有一行：否则界面上"这一步没发生"和"这一步被拦下"长得一样，
                            # 而后者是用户该看见的（审批刚做完就静默消失，是最容易被误读的一种反馈）。
                            from codeharness.report import tool_call_report
                            await tool_call_report(name, args, "", ok=False)
                            continue
                        out = await asyncio.wait_for(self.tools[name].ainvoke(args), timeout=180)
                        # 每一步都发一行（C6/对话流：只读类工具原本一个块都不发 ⇒ "每步一行"无从谈起）。
                        # 只发事实（工具名+参数摘要+结果首行），动词与摘要由前端派生。
                        from codeharness.report import tool_call_report
                        await tool_call_report(name, args, out)
                        results.append({"name": name, "result": str(out)[:4000]})
                    else:
                        # T4-③：这是「召回/名册漏了」这件事在线上**唯一数得出来**的信号——命中率要真值才算得出
                        # （线上没有真值），但「模型要的命令不在它这一轮看见的工具面里」是一条就是一次回喂 +
                        # 多烧一发（实测 ¥0.12–0.27）。留一行可 grep 的话，纪律同 `[session-failed]`。
                        logger.warning(f"[unknown-command] 模型要的命令不在本轮工具面：{name}"
                                       f"（在册 {len(self.tools)} 只，task={str(s.get('task'))[:40]!r}）")
                        # 计数落到账本上（T4-③ 的聚合面）：事件流是喂界面的，日志行是给人 grep 的，
                        # 而"这场会话白烧了几发"要能被 GET 读到、要能跨会话求和，就得有个唯一出口。
                        cm = getattr(self.llm, "cost_manager", None)
                        if cm is not None:
                            cm.unknown_command_calls += 1
                        results.append({"name": name, "result": f"未知命令 {name}，可用: {list(self.tools)}"})
                except GraphInterrupt:
                    raise                                         # interrupt 靠抛异常暂停图——绝不能被 self-heal 吞掉
                except asyncio.TimeoutError:
                    results.append({"name": name, "result": f"[超时] {name}"})
                except Exception as e:                            # self-heal：错误回喂下一轮（源 :289 error_msg 同语义）
                    results.append({"name": name, "result": f"[错误] {type(e).__name__}: {e}"})
            history = s["history"][:-1] + [{**last, "results": results}]
            # 游标：停在 ask 上就留在那一条（`_ask` 读它、并把游标推到下一条）；跑完就推到列表末尾。
            # `pending_ask` 是 `act → ?` 那条条件边的判据（见 `build()` 里的 `_after_act`）。
            return {"history": history, "finished": finished,
                    "act_cursor": pending_at if pending_at >= 0 else len(commands),
                    "pending_ask": pending_at >= 0}
        finally:
            var_child_runnable_config.reset(tok)

    async def _ask(self, s: RoleZeroState):
        """`RoleZero.ask_human` 的落点（C59）。**零副作用**——所以恢复时重放它无害，这正是它存在的理由。

        只做三件事：读游标处那条命令的问题、`interrupt()` 问人、把答案记进本轮结果并把游标 +1。
        恢复时 LangGraph 会把这个函数从头再跑一遍：`interrupt()` 这一次不再暂停，而是**直接返回**上次的
        答复值，于是继续往下走——所以这里的每一句都必须是幂等的（读 state / 拼结果 / 写回）。
        真正的副作用在 `_act` 里，而它靠游标跳过已跑完的那截。

        ⚠ 出口是 `build()` 里的静态边 `ask → gate`（不是直接回 `act`）：问完之后剩下的命令还没过审批闸门。
        """
        last = s["history"][-1]
        idx = int(s.get("act_cursor") or 0)
        cmd = last["commands"][idx]
        answer = interrupt({"question": cmd.get("args", {}).get("question", "")})
        results = list(last.get("results") or []) + [{"name": "RoleZero.ask_human", "result": answer}]
        return {"history": s["history"][:-1] + [{**last, "results": results}],
                "act_cursor": idx + 1, "pending_ask": False}

    # ---- 嵌入团队图（接口与 Agent.as_node 完全一致） ----
    def as_node(self, name: str):
        graph = self.build()

        async def _run(state: dict):
            from codeharness.report import set_role
            set_role(name)                      # 报道事件的 role 字段
            inbox = state.get("_inbox") or []
            incoming = inbox[-1] if inbox else None
            # 回报（成员→队长）不是新任务：清了计划，队长就没法 finish_current_task 了。
            # 委派出去的载荷不带这个标记（_wire_delegation 只留 content/sent_from），仍按新任务走。
            is_report = incoming is not None and incoming.instruct_schema == "TeamReport"
            task = incoming.content if incoming else "continue"
            if is_report:
                self.memory.add(Message(content=f"[{incoming.sent_from} 的回报] {task}", role="user",
                                        sent_from=incoming.sent_from,
                                        cause_by=RequirementTag.RUN_COMMAND))
            elif task != "continue":
                # 新任务→旧计划作废（源：每任务 planner 重立）；"continue" 保计划续跑。
                # ⚠ 任务必须进 self.memory：_context_messages 只从记忆取材，think 的 prompt 里没有
                # 任务文本——不装则模型上下文根本没有需求（S9.1 对照首跑实测：真模型第一条思考
                # 就是「没有具体用户需求，先问用户」，零产物收口=第十七处）。
                self.plan = None
                self._plan_goal = task
                self.memory.add(Message(content=task, role="user", sent_from="user",
                                        cause_by=RequirementTag.USER_REQUIREMENT))
                # 谁派给我的，我就向谁回报（源 MGXEnv 靠全员广播让队长自己看见；本仓 `<all>` 刻意
                # 不广播，所以指名回报。`sent_from=="user"` 时不回报——那本来就是用户直接递的活）。
                self._report_to = incoming.sent_from if incoming.sent_from not in ("", "user", name) else ""
                if self.plan_fn is not None:
                    # 批次1：ToT 等外部规划器——先树搜索出择优路径，写进记忆供 think 取材（源 ToT 无角色
                    # 消费者，本仓把它接到 RoleZero 首轮规划这一真实接缝上）
                    try:
                        plan_text = await self.plan_fn(task)
                        if plan_text:
                            self.memory.add(Message(content=f"[Planned path]\n{plan_text}", role="assistant",
                                                    cause_by=RequirementTag.USER_REQUIREMENT))
                    except Exception as e:
                        logger.warning(f"plan_fn({type(self.plan_fn).__name__}) 失败，退回无规划: {type(e).__name__}: {e}")
            sub = await graph.ainvoke({"task": task, "history": [], "experience": "",
                                       "respond_language": "中文", "finished": False,
                                       # C59：新一跑从第 0 条命令开始（不传也行——`_act`/`_ask`/`gate`
                                       # 都按 `s.get("act_cursor") or 0` 兜底，这里写出来只为读代码时看得见）
                                       "act_cursor": 0})
            turns = sub["history"] or []
            # `reply_to_human` 可能在任意一轮（模型常是「先汇报、再 end」），只读最后一轮会把成员
            # 干完活的那句话蒸发在收尾 thought 里（实测：给成员排两轮脚本，黑板收到的是第二轮的「收工」）
            reply = next((r["result"] for t in reversed(turns) for r in reversed(t.get("results") or [])
                          if r["name"] == "RoleZero.reply_to_human"), None)
            content = reply or (turns[-1]["thought"] if turns else "done")
            to, self._report_to = self._report_to, ""
            msgs = [Message(content=content, role="assistant",
                            cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                            send_to={to} if to else None,
                            instruct_schema="TeamReport" if to else "")]
            # ---- 委派聚合件（C1-②）：源 publish_team_message 一次一发，本仓一次节点运行攒一摞，
            # 交给 team_graph 的 `_wire_delegation` 拆成「一人一条」再 Send。投给自己不算委派（源同）。
            deleg = [(c, m) for c, m in self._outbox if m != name]
            self._outbox = []
            if deleg:
                # 顺序有讲究：route() 只读 state["messages"][-1]，聚合件必须排在最后一条。
                # ponytail: 同一轮既委派又回报时，只有排最后的那条被路由（本仓一次超步只投递一条消息）；
                # 上限=队长自己不收成员回报，要同轮双投递得让 route 改读「本超步新增的全部消息」。
                msgs.append(Message(content="\n".join(f"→ {m}: {c[:120]}" for c, m in deleg),
                                    role="user", cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                                    send_to={m for _, m in deleg},
                                    instruct_content={"delegations": [{"member": m, "instruction": c}
                                                                      for c, m in deleg]},
                                    instruct_schema="TeamDelegation"))
            return {"messages": msgs}

        return name, _run
