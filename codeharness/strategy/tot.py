"""ToT (Tree of Thoughts) 策略：源 strategy/tot.py(277) 的 `改`移植。

源事实（已核实）：`ThoughtSolverBase` = generate→evaluate→select→update 四拍；`BFSSolver`
逐层扩展 + 贪心选 top-N；`DFSSolver` 一路深入、连续 2 个 invalid 即回溯 break；
`MCTSSolver` 在源即 `raise NotImplementedError`。**本仓移植 BFS + DFS 两种，MCTS 判弃**
（登记 `判定-复制与重构清单.md` §四：源本身是空壳，无实现可搬）。

技术栈适配：
- LLM 走本仓 `LLMGateway.aask(msg, tag=)`（源是 `self.llm.aask(msg=…)`，位置参兼容 FakeLLM）。
- 源用 `eval(thoughts)` 解析模型输出——**改 `ast.literal_eval`**（模型输出不可信，eval 是 RCE 面）。
- 源 parser/evaluator 是抽象可插拔件（具体实现在 examples/GameOf24 里）。本仓附一套最简 concrete
  `SimpleParser`/`NumericEvaluator`，让树搜索开箱可跑、可被门禁断言（ponytail 的一枚 runnable check）。
"""
from __future__ import annotations

import ast
import asyncio
import re
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from codeharness.logs import logger
from codeharness.strategy.base import ThoughtNode, ThoughtTree

# 源 tot.py:19-29 OUTPUT_FORMAT（逐字语义：模型回一个 node 列表 JSON）
OUTPUT_FORMAT = """
Each output should be strictly a list of nodes, in json format, like this:
```json
    [
        {
            "node_id": "unique identifier for a solution, can be an ordinal",
            "node_state_instruction": "specified sample of solution",
        },
        ...
    ]
```
"""


class MethodSelect(Enum):
    GREEDY = "greedy"          # SAMPLE 在源即 NotImplementedError，不移植


class Strategy(Enum):
    BFS = "BFS"
    DFS = "DFS"
    # MCTS = "MCTS"            # 判弃：源 solver/tot 里 MCTSSolver 是空壳


class SimpleParser:
    """最简 parser：propose 生成候选、__call__ 取状态、value 出评估 prompt。对齐源 BaseParser 三法。"""

    def __call__(self, current_state: str, **kw) -> str:
        return current_state

    def propose(self, current_state: str, n_generate_sample: int = 5, **kw) -> str:
        return (f"Current state: {current_state}\n"
                f"Propose up to {n_generate_sample} distinct next-step solution candidates.")

    def value(self, input: str, node_id: Any = None, **kw) -> str:
        return (f"Score this partial solution 0-10 (higher = closer to solving the problem).\n"
                f"Solution: {input}\nReturn ONLY an integer.")


class NumericEvaluator:
    """从模型回复里取 0-10 整数；>= threshold 视为 valid。对齐源 BaseEvaluator __call__/status_verify。"""

    threshold = 1

    def __call__(self, text: str, node_id: Any = None, **kw) -> float:
        m = re.search(r"\d+", str(text))
        return float(int(m.group()) if m else 0)

    def status_verify(self, value: float) -> bool:
        return value >= self.threshold


class ThoughtSolverConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    strategy: Strategy = Strategy.BFS
    max_steps: int = 3
    method_select: MethodSelect = MethodSelect.GREEDY
    n_generate_sample: int = 5      # 每节点生成候选数
    n_select_sample: int = 3        # 每层保留 top-N（贪心束宽）
    parser: Any = Field(default_factory=SimpleParser)
    evaluator: Any = Field(default_factory=NumericEvaluator)


class ThoughtSolverBase(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    thought_tree: Optional[ThoughtTree] = None
    llm: Any = None                 # LLMGateway / FakeLLM
    config: ThoughtSolverConfig = Field(default_factory=ThoughtSolverConfig)

    async def solve(self, init_prompt: str = "") -> List[str]:
        raise NotImplementedError

    async def generate_thoughts(self, current_state: str = "", current_node: Optional[ThoughtNode] = None) -> List[ThoughtNode]:
        state_prompt = self.config.parser.propose(
            current_state=current_state, n_generate_sample=self.config.n_generate_sample)
        rsp = await self.llm.aask(state_prompt + "\n" + OUTPUT_FORMAT, tag="tot_generate")
        thoughts = self._parse_node_list(rsp)
        return self.thought_tree.update_node(thoughts, current_node=current_node)

    @staticmethod
    def _parse_node_list(rsp: str) -> List[dict]:
        """从 ```json ...``` 取列表。源用 eval；本仓改 literal_eval（模型输出不可信）。"""
        from codeharness.utils.common import CodeParser
        raw = CodeParser.parse_code(text=rsp, block="json") or CodeParser.parse_code(text=rsp) or str(rsp)
        try:
            val = ast.literal_eval(raw.strip())
        except Exception:
            try:
                val = ast.literal_eval(raw[raw.index("["):raw.rindex("]") + 1])
            except Exception:
                logger.warning(f"ToT generate 输出无法解析为节点列表，退化为空: {raw[:80]!r}")
                val = []
        return val if isinstance(val, list) else []

    async def evaluate_node(self, node: ThoughtNode, parent_value: float) -> None:
        eval_prompt = self.config.parser.value(input=node.name, node_id=node.id)
        evaluation = await self.llm.aask(eval_prompt, tag="tot_evaluate")
        value = self.config.evaluator(evaluation, node_id=node.id)
        node.update_valid_status(status=self.config.evaluator.status_verify(value))
        node.update_value(parent_value + value)          # 累计分（源语义）

    def select_nodes(self, thought_nodes: List[ThoughtNode]) -> List[ThoughtNode]:
        """贪心：按累计 value 降序留 top-N，其余从树上摘除（parent=None）。"""
        if self.config.method_select != MethodSelect.GREEDY:
            raise NotImplementedError(f"method_select={self.config.method_select} 未移植")
        keep = sorted(thought_nodes, key=lambda x: x.value, reverse=True)[: self.config.n_select_sample]
        for node in thought_nodes:
            if node not in keep:
                node.valid_status = False
                if node.parent is not None and node in node.parent.children:
                    node.parent.children.remove(node)
        return keep

    def update_solution(self):
        """全树取累计分最高节点，回溯成路径。"""
        best = max(self.thought_tree.all_nodes, key=lambda x: x.value, default=None)
        return [best], self.thought_tree.parse_node_path(best)


class BFSSolver(ThoughtSolverBase):
    """逐层扩展 → 贪心选束 → 重复 max_steps，最后取最优叶。对齐源 BFSSolver。"""

    async def solve(self, init_prompt: str = "") -> List[str]:
        root = ThoughtNode(init_prompt)
        self.thought_tree = ThoughtTree(root)
        current_nodes = [root]
        for _ in range(self.config.max_steps):
            solutions = await self._bfs_build(current_nodes)
            if not solutions:
                break
            current_nodes = self.select_nodes(solutions)
            if not current_nodes:
                break
        _, best_path = self.update_solution()
        logger.info(f"best solution is: {best_path}")
        return best_path

    async def _bfs_build(self, current_nodes: List[ThoughtNode]) -> List[ThoughtNode]:
        tasks = [self.generate_and_evaluate_nodes(self.config.parser(n.name), n.value, n) for n in current_nodes]
        per_node = await asyncio.gather(*tasks)
        return [child for kids in per_node for child in kids]

    async def generate_and_evaluate_nodes(self, current_state, current_value, node):
        thought_nodes = await self.generate_thoughts(current_state, current_node=node)
        await asyncio.gather(*(self.evaluate_node(c, parent_value=current_value) for c in thought_nodes))
        return thought_nodes


class DFSSolver(ThoughtSolverBase):
    """一路沿 thought_nodes[0] 深入，连续 2 个 invalid 即 break。对齐源 DFSSolver。"""

    async def _dfs(self, root_node: ThoughtNode) -> List[str]:
        impossible = 0
        node = root_node
        for _ in range(self.config.max_steps):
            thought_nodes = await self.generate_thoughts(self.config.parser(node.name), current_node=node)
            if not thought_nodes:
                break
            first = thought_nodes[0]
            await self.evaluate_node(first, parent_value=node.value)
            if not first.valid_status:
                impossible += 1
                if impossible >= 2:
                    logger.info("impossible state reached, break")
                    break
            else:
                impossible = 0
            node = first
        return self.thought_tree.parse_node_path(node)

    async def solve(self, init_prompt: str = "") -> List[str]:
        root = ThoughtNode(init_prompt)
        self.thought_tree = ThoughtTree(root)
        path = await self._dfs(root)
        return path


def make_tot_planner(llm, strategy: Strategy = Strategy.BFS, max_steps: int = 2,
                     n_generate_sample: int = 5, n_select_sample: int = 3):
    """给 RoleZero 的 plan_fn 工厂：吃 goal → 跑一次 ToT 树搜索 → 返回最优路径文本。

    源里 ToT 无角色消费者（框架件，靠 examples 的 parser 落地）。本仓把它接到 RoleZero 首轮规划：
    `plan_fn(goal)` 产出的路径字符串写进 memory，让后续 think 带着这份"择优后的规划"走。
    """
    cfg = ThoughtSolverConfig(strategy=strategy, max_steps=max_steps,
                              n_generate_sample=n_generate_sample, n_select_sample=n_select_sample)

    async def plan(goal: str) -> str:
        solver = BFSSolver(llm=llm, config=cfg) if strategy == Strategy.BFS else DFSSolver(llm=llm, config=cfg)
        path = await solver.solve(init_prompt=f"Goal: {goal}")
        return "\n".join(f"- {step}" for step in path if step)

    return plan
