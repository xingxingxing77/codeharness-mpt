"""strategy：plan-and-act 引擎（源 strategy/planner.py + DataInterpreter 的完整形态）。
Planner 用 structured 产出任务计划；PlanAndActAgent 按 plan 逐任务执行（分析代码→沙箱运行）。
人在环评审闸门（接线台账 #12）：源 planner.py 的两道 ask_review——:96 计划确认、:104/:143
结果验收（confirm_task 游标才前进）。源的阻塞 get_human_input 换成 R5 的 interrupt；
CONTINUE_WORDS 词表逐字照源 ask_review.py:13。
图形态说明：interrupt 恢复会**重放所在节点**，闸门必须是独立无 LLM 调用的节点（plan_review/accept），
"重规划"回到 plan 节点重进——不能把 while+interrupt 写进 _plan（resume 后会把模型再烧一遍）。
auto_run=True（缺省）= 源的自动模式：成败即放行、全程零 interrupt；auto_run=False 开双闸。"""
import json
from typing import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from codeharness.schema import Message
from codeharness.actions.data_analysis import WriteAnalysisCode, RunPythonCode

# 源 actions/di/ask_review.py:13 逐字
CONTINUE_WORDS = ["confirm", "continue", "c", "yes", "y"]


def _confirmed(reply) -> bool:
    """源 ask_review.py:60 判定：整句命中 CONTINUE_WORDS 或句中含 "confirm"。"""
    low = str(reply).strip().lower()
    return low in CONTINUE_WORDS or CONTINUE_WORDS[0] in low


class Task(BaseModel):
    """对齐源 strategy/planner 的 Task 与 prompts/di/role_zero.py 的 Task 数据结构"""
    task_id: str = ""
    dependent_task_ids: list[str] = Field(default_factory=list)
    instruction: str = ""
    assignee: str = ""


class Plan(BaseModel):
    goal: str = ""
    tasks: list[Task] = Field(default_factory=list)


class Planner:
    """源 strategy/planner.py 的等价物：goal → 任务计划（structured 强约束）"""

    PLAN_PROMPT = """You are a planner. Decompose the goal into 1-5 sequential tasks.
Each task: task_id (T1..Tn), dependent_task_ids (prior task ids), instruction (concrete, executable
by writing and running ONE Python script), assignee ("David")."""

    def __init__(self, llm):
        self.llm = llm

    async def plan(self, goal: str) -> Plan:
        plan = await self.llm.structured(Plan).ainvoke(f"{self.PLAN_PROMPT}\n\nGoal: {goal}")
        plan.goal = goal
        return plan


class PlanAndActState(TypedDict):
    goal: str
    plan: dict
    task_idx: int
    code: str
    results: list
    finished: bool
    plan_ok: bool        # 人工确认过的计划（auto 模式在 _plan 直接置 True）
    feedback: str        # 未确认时回喂重规划的人话（源 working_memory.add(review) 的位置）


class PlanAndActAgent:
    """DataInterpreter 的完整形态：plan →(人工确认)→ 逐任务（写分析代码→沙箱执行→验收）→ 汇总。
    接口与 Agent.as_node 一致（registry/team 直接嵌入）。"""

    def __init__(self, profile: dict, llm, max_tasks: int = 5, auto_run: bool = True):
        self.profile = profile
        self.llm = llm
        self.planner = Planner(llm)
        self.max_tasks = max_tasks
        self.auto_run = auto_run
        self.writer = WriteAnalysisCode(llm=llm)
        self.runner = RunPythonCode(llm=llm)

    def build(self, checkpointer=None):
        g = StateGraph(PlanAndActState)
        g.add_node("plan", self._plan)
        g.add_node("plan_review", self._plan_review)
        g.add_node("execute", self._execute)
        g.add_node("accept", self._accept)        # 唯一游标前进点（源 confirm_task:143-146 的位）
        g.add_node("summarize", self._summarize)
        g.set_entry_point("plan")
        g.add_conditional_edges("plan", self._after_plan)
        g.add_conditional_edges("plan_review", lambda s: "execute" if s["plan_ok"] else "plan")
        g.add_edge("execute", "accept")
        g.add_conditional_edges("accept", lambda s: "summarize" if s["finished"] else "execute")
        g.add_edge("summarize", END)
        return g.compile(checkpointer=checkpointer or InMemorySaver())

    def _after_plan(self, s: PlanAndActState):
        if not s["plan"].get("tasks"):
            return "summarize"
        return "execute" if s["plan_ok"] else "plan_review"

    async def _plan(self, s: PlanAndActState):
        goal = s["goal"] + (f"\nUser review feedback: {s['feedback']}" if s.get("feedback") else "")
        plan = await self.planner.plan(goal)
        tasks = plan.model_dump()["tasks"][:self.max_tasks]
        return {"plan": {"goal": plan.goal, "tasks": tasks}, "task_idx": 0,
                "results": [], "finished": False, "plan_ok": self.auto_run}

    async def _plan_review(self, s: PlanAndActState):
        """闸 1（源 planner.py:96）：resume 值=人话；确认才执行，否则带反馈回 plan 重规划。"""
        reply = interrupt({"review": "plan", "goal": s["goal"], "tasks": s["plan"]["tasks"]})
        return {"plan_ok": _confirmed(reply), "feedback": "" if _confirmed(reply) else str(reply)}

    async def _execute(self, s: PlanAndActState):
        tasks = s["plan"]["tasks"]
        idx = s["task_idx"]
        instruction = tasks[idx]["instruction"]
        code_msg = await self.writer.run(Message(content=instruction, cause_by="PlanAndAct"))
        run_msg = await self.runner.run(Message(content=code_msg.content, cause_by=code_msg.cause_by))
        return {"results": s["results"] + [{"task": instruction, "output": run_msg.content}]}

    async def _accept(self, s: PlanAndActState):
        """闸 2（源 :104 process_task_result）：auto 按执行成败推进；manual 人不点头游标不动（重做当前任务）。"""
        last = s["results"][-1] if s["results"] else {}
        if "rc=1" in str(last.get("output", "")):
            return {"finished": True}                       # 失败即停（可接 replan，源同语义）
        if not self.auto_run:
            reply = interrupt({"review": "task", "task": last.get("task", ""),
                               "output": str(last.get("output", ""))[:2000]})
            if not _confirmed(reply):
                return {"finished": False}
        nxt = s["task_idx"] + 1
        return {"task_idx": nxt, "finished": nxt >= len(s["plan"]["tasks"])}

    async def _summarize(self, s: PlanAndActState):
        summary = await self.llm.aask(
            "汇总以下任务执行结果，输出数据分析结论：\n" +
            json.dumps(s["results"], ensure_ascii=False)[:6000], tag="DataInterpreter")
        return {"results": s["results"] + [{"task": "__summary__", "output": summary}]}

    def as_node(self, name: str):
        graph = self.build()

        async def _run(state: dict):
            from codeharness.report import set_role
            set_role(name)
            inbox = state.get("_inbox") or []
            goal = inbox[-1].content if inbox else "continue"
            # 不传 configurable：子图从外层节点的 runnable config 继承 checkpoint 命名空间，
            # 外层的 Command(resume) 才能进到 interrupt() 里（role_zero.as_node 同一构）
            sub = await graph.ainvoke({"goal": goal, "plan": {}, "task_idx": 0,
                                       "code": "", "results": [], "finished": False,
                                       "plan_ok": False, "feedback": ""})
            summary = next((r["output"] for r in reversed(sub["results"])
                            if r["task"] == "__summary__"), "done")
            return {"messages": [Message(content=summary, role="assistant",
                                         cause_by="RunCommand", sent_from=name)]}

        return name, _run
