"""strategy：plan-and-act 引擎（源 strategy/planner.py + DataInterpreter 的完整形态）。
Planner 用 structured 产出任务计划；PlanAndActAgent 按 plan 逐任务执行（分析代码→沙箱运行）。"""
import json
from typing import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from codeharness.schema import Message
from codeharness.actions.data_analysis import WriteAnalysisCode, RunPythonCode


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


class PlanAndActAgent:
    """DataInterpreter 的完整形态：plan → 逐任务（写分析代码→沙箱执行）→ 汇总。
    接口与 Agent.as_node 一致（registry/team 直接嵌入）。"""

    def __init__(self, profile: dict, llm, max_tasks: int = 5):
        self.profile = profile
        self.llm = llm
        self.planner = Planner(llm)
        self.max_tasks = max_tasks
        self.writer = WriteAnalysisCode(llm=llm)
        self.runner = RunPythonCode(llm=llm)

    def build(self, checkpointer=None):
        g = StateGraph(PlanAndActState)
        g.add_node("plan", self._plan)
        g.add_node("execute", self._execute)
        g.add_node("summarize", self._summarize)
        g.set_entry_point("plan")
        g.add_conditional_edges("plan", lambda s: "execute" if s["plan"].get("tasks") else "summarize")
        g.add_conditional_edges("execute", lambda s: "summarize" if s["finished"] else "execute")
        g.add_edge("summarize", END)
        return g.compile(checkpointer=checkpointer or InMemorySaver())

    async def _plan(self, s: PlanAndActState):
        plan = await self.planner.plan(s["goal"])
        tasks = plan.model_dump()["tasks"][:self.max_tasks]
        return {"plan": {"goal": plan.goal, "tasks": tasks}, "task_idx": 0,
                "results": [], "finished": False}

    async def _execute(self, s: PlanAndActState):
        tasks = s["plan"]["tasks"]
        idx = s["task_idx"]
        instruction = tasks[idx]["instruction"]
        code_msg = await self.writer.run(Message(content=instruction, cause_by="PlanAndAct"))
        run_msg = await self.runner.run(Message(content=code_msg.content, cause_by=code_msg.cause_by))
        results = s["results"] + [{"task": instruction, "output": run_msg.content}]
        nxt = idx + 1
        return {"results": results, "task_idx": nxt,
                "finished": nxt >= len(tasks) or "rc=1" in run_msg.content}   # 失败即停（可接 replan）

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
            sub = await graph.ainvoke({"goal": goal, "plan": {}, "task_idx": 0,
                                       "code": "", "results": [], "finished": False})
            summary = next((r["output"] for r in reversed(sub["results"])
                            if r["task"] == "__summary__"), "done")
            return {"messages": [Message(content=summary, role="assistant",
                                         cause_by="RunCommand", sent_from=name)]}

        return name, _run
