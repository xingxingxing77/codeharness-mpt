"""RoleZero：动态范式（源 roles/di/role_zero.py:55-475 的语义 → 图）。
_think(:198) 八步组装 → think 节点；_act(:280) 命令执行 → act 节点；
ask_human(:456)/reply_to_human(:465)/_end(:474) → interrupt/记录/END。
工作记忆照源 :241 `memory.get(memory_k)` 窗口回喂，溢出交 BrainMemory 摘要（S5.1）；
经验检索(_retrieve_experience:449) 接第 8 步 exp_pool，此版留空串占位。"""
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
from codeharness.logs import logger
from codeharness.memory.brain_memory import BrainMemory
from codeharness.memory.memory import Memory
from codeharness.schema import Message, Command
from codeharness.prompts.role_zero import (SYSTEM_PROMPT, CMD_PROMPT, ROLE_INSTRUCTION,
                                           TASK_TYPE_DESC)


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


class RoleZero:
    SPECIAL = {"end", "End", "RoleZero.ask_human", "RoleZero.reply_to_human"}

    def __init__(self, profile: dict, tools: list, llm, system_prompt: str = SYSTEM_PROMPT,
                 instruction: str = ROLE_INSTRUCTION, max_loops: int = 15, env_desc: str = "",
                 longterm_memory=None, memory: Memory | None = None,
                 brain: BrainMemory | None = None, redis_key: str = "", memory_k: int = 0):
        self.profile = profile
        self.tools = {t.name: t for t in tools}
        self.llm = llm
        self.system_prompt = system_prompt
        self.instruction = instruction
        self.max_loops = max_loops
        self.env_desc = env_desc
        self.ltm = longterm_memory          # 第 4 步 LongTermMemory，可空
        self.memory = memory if memory is not None else Memory()
        self.brain = brain                  # None → 超窗直接丢，语义同源的 memory_k 截断
        self.redis_key = redis_key
        self.memory_k = memory_k or settings.memory_overflow_size
        self._brain_loaded = False

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
        """超窗：窗口外那截交给 BrainMemory 滚动摘要并落 Redis，进程内只留窗口。"""
        if self.brain is None or len(self.memory.storage) <= self.memory_k:
            return
        evicted = self.memory.storage[:-self.memory_k]
        self.memory.storage = self.memory.storage[-self.memory_k:]
        for m in evicted:
            self.brain.add_history(m)
        try:
            await self.brain.summarize(self.llm, redis_key=self._brain_key())
        except ValueError as e:                     # 摘要没产出不能把记忆丢了——退回原样，下轮再试
            logger.warning(f"{self.profile['name']} 记忆压缩失败，保留窗口外 {len(evicted)} 条不裁剪: {e}")
            self.memory.storage = evicted + self.memory.storage

    def _context_messages(self) -> list:
        out = []
        if self.brain is not None and self.brain.historical_summary:
            out.append(SystemMessage(content=f"[历史摘要] {self.brain.historical_summary}"))
        for m in self.memory.get(self.memory_k):
            out.append(AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content))
        return out

    # ---- 源 _get_prefix(:276)：人设 + 当前时间 ----
    def _prefix(self) -> str:
        p = self.profile
        s = (f"You are a {p.get('profile', 'helper')}, named {p['name']}, "
             f"your goal is {p.get('goal', '')}.")
        if self.env_desc:
            s += f" You are in {self.env_desc}."
        s += f" The current time is {datetime.now():%Y-%m-%d %H:%M:%S}."
        return s

    def _plan_status(self, s: RoleZeroState):
        """源 :216 get_plan_status 的极简版：Task 数据结构在 prompt 里，计划即 history 里的 Plan 命令记录"""
        lines = [f"- [{i+1}] {h['thought'][:80]}" for i, h in enumerate(s["history"][-5:])]
        return "\n".join(lines) or "(no plan yet)", f"step {len(s['history'])+1}/{self.max_loops}"

    def build(self, checkpointer=None):
        g = StateGraph(RoleZeroState)
        g.add_node("think", self._think)
        g.add_node("act", self._act)
        g.set_entry_point("think")
        g.add_conditional_edges("think", lambda s: END if s["finished"] else "act")
        g.add_edge("act", "think")
        return g.compile(checkpointer=checkpointer or InMemorySaver())

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
            memories = await self.ltm.recall(s["task"], k=3)
            experience = "\n".join(m.content for m in memories)

        tool_info = json.dumps({n: {"description": t.description} for n, t in self.tools.items()},
                               ensure_ascii=False)
        plan_status, current_task = self._plan_status(s)
        system_prompt = self.system_prompt.format(
            role_info=self._prefix(), task_type_desc=TASK_TYPE_DESC,
            available_commands=tool_info, example=experience, instruction=self.instruction)
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
            context = [SystemMessage(content=system_prompt), *self._context_messages()]
            try:
                thought: ZeroThought = await self.llm.structured(ZeroThought).ainvoke(
                    context + [HumanMessage(content=prompt)])
            except Exception:
                # structured 失败 → 纯文本重问 + repair 管线 + LLM 自修（源 parse_commands + JSON_REPAIR 链）
                from codeharness.provider.repair import llm_repair_json
                raw = await self.llm.aask([*context, HumanMessage(content=prompt)], tag="rz_fallback")
                thought = llm_repair_json(raw, ZeroThought, self.llm) or ZeroThought(
                    thought=f"[解析失败，已按 end 处理] {raw[:200]}",
                    commands=[{"command_name": "end", "args": {}}])
            await rep.content(thought.thought)
        commands = [c.model_dump() for c in thought.commands]
        if not commands:                                      # 契约：至少一条命令，否则视作结束
            commands = [{"command_name": "end", "args": {}}]
        self.memory.add(Message(content=thought.thought, role="assistant",
                                cause_by=RequirementTag.RUN_COMMAND, sent_from=self.profile["name"]))
        return {"history": s["history"] + [{"thought": thought.thought, "commands": commands}]}

    # ---- 源 _act(:280-301)/_run_commands(:385)/_run_special_command(:420) ----
    # config 参数由 LangGraph 注入；interrupt() 的 get_config 依赖它（langgraph 1.x 节点执行路径不自带）
    async def _act(self, s: RoleZeroState, config: RunnableConfig | None = None):
        from langchain_core.runnables.config import var_child_runnable_config
        tok = var_child_runnable_config.set(config)
        try:
            last = s["history"][-1]
            results, finished = [], False
            for cmd in last["commands"]:
                name, args = cmd["command_name"], cmd.get("args", {})
                try:
                    if name in ("end", "End"):                    # 源 _end(:474)
                        finished = True
                        results.append({"name": name, "result": "[结束]"})
                    elif name == "RoleZero.ask_human":            # 源 ask_human(:456) → interrupt
                        answer = interrupt({"question": args.get("question", "")})
                        results.append({"name": name, "result": answer})
                    elif name == "RoleZero.reply_to_human":       # 源 reply_to_human(:465)
                        results.append({"name": name, "result": f"[已回复] {args.get('content', '')}"})
                    elif name == "Plan.finish_current_task":
                        results.append({"name": name, "result": "[任务完成]"})
                    elif name in self.tools:
                        out = await asyncio.wait_for(self.tools[name].ainvoke(args), timeout=180)
                        results.append({"name": name, "result": str(out)[:4000]})
                    else:
                        results.append({"name": name, "result": f"未知命令 {name}，可用: {list(self.tools)}"})
                except GraphInterrupt:
                    raise                                         # interrupt 靠抛异常暂停图——绝不能被 self-heal 吞掉
                except asyncio.TimeoutError:
                    results.append({"name": name, "result": f"[超时] {name}"})
                except Exception as e:                            # self-heal：错误回喂下一轮（源 :289 error_msg 同语义）
                    results.append({"name": name, "result": f"[错误] {type(e).__name__}: {e}"})
            history = s["history"][:-1] + [{**last, "results": results}]
            return {"history": history, "finished": finished}
        finally:
            var_child_runnable_config.reset(tok)

    # ---- 嵌入团队图（接口与 Agent.as_node 完全一致） ----
    def as_node(self, name: str):
        graph = self.build()

        async def _run(state: dict):
            from codeharness.report import set_role
            set_role(name)                      # 报道事件的 role 字段
            inbox = state.get("_inbox") or []
            task = inbox[-1].content if inbox else "continue"
            sub = await graph.ainvoke({"task": task, "history": [], "experience": "",
                                       "respond_language": "中文", "finished": False})
            results = sub["history"][-1]["results"] if sub["history"] else []
            reply = next((r["result"] for r in results if r["name"] == "RoleZero.reply_to_human"), None)
            content = reply or (sub["history"][-1]["thought"] if sub["history"] else "done")
            return {"messages": [Message(content=content, role="assistant",
                                         cause_by=RequirementTag.RUN_COMMAND, sent_from=name)]}

        return name, _run
