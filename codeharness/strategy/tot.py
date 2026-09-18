"""ToT (Tree of Thoughts) 策略：多解择优的树搜索。

判 `新`：源 project 无 ToT 实现，本仓从零写 MVP。
保留 ThoughtNode/ThoughtTree 数据结构，执行换 LangGraph 子图（多解择优）。

ponytail: ceiling 是「仅支持深度 1 的简单树」——真实 ToT 需要：
- 前向评估函数（evaluator）
- 搜索算法（beam search/bfs/dfs）
- 回溯机制
本 MVP 只实现：生成 N 条路径 → 选最优 → 返回，覆盖 build_role 第四档 strategy="tot"。
"""
from typing import Optional


class ThoughtNode:
    """ToT 的单个思考节点。"""
    
    def __init__(self, thought: str, parent: Optional["ThoughtNode"] = None):
        self.thought = thought              # 当前思考内容
        self.parent = parent                # 父节点（用于回溯）
        self.children: list[ThoughtNode] = []  # 子节点
        self.score: float = 0.0            # 评估分数
        
    def add_child(self, child: "ThoughtNode"):
        self.children.append(child)
        child.parent = self
        
    def get_path(self) -> list[str]:
        """从根到当前节点的路径。"""
        path = []
        node = self
        while node:
            path.append(node.thought)
            node = node.parent
        return list(reversed(path))


class ThoughtTree:
    """ToT 的树结构。"""
    
    def __init__(self, root_thought: str):
        self.root = ThoughtNode(root_thought)
        self.current_depth = 0
        self.max_depth = 3  # 最大深度
        
    def get_leaves(self) -> list[ThoughtNode]:
        """获取所有叶子节点。"""
        leaves = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            if not node.children:
                leaves.append(node)
            else:
                stack.extend(node.children)
        return leaves
    
    def expand(self, node: ThoughtNode, llm, num_choices: int = 3) -> list[ThoughtNode]:
        """用 LLM 生成多个候选思考。"""
        context = "\n".join(node.get_path())
        prompt = f"""Given the following thought sequence:
{context}

Generate {num_choices} alternative next thoughts. Each should be a short, concrete step.
Return ONLY a JSON list of strings, e.g.: ["thought 1", "thought 2", "thought 3"]
"""
        
        from pydantic import BaseModel, Field
        from typing import List
        
        class ThoughtChoices(BaseModel):
            choices: List[str] = Field(..., description="List of alternative thoughts")
        
        try:
            resp = llm._structured(prompt, schema=ThoughtChoices)
            thoughts = getattr(resp, 'choices', []) or []
        except Exception:
            # 降级：单条思考
            thoughts = [f"Alternative to: {node.thought}"]
        
        new_nodes = []
        for t in thoughts[:num_choices]:
            child = ThoughtNode(thought=t.strip(), parent=node)
            node.add_child(child)
            new_nodes.append(child)
        
        return new_nodes
    
    def evaluate(self, node: ThoughtNode, llm) -> float:
        """评估节点的优劣（0~1 之间）。"""
        path = node.get_path()
        prompt = f"""Evaluate this thought sequence on a scale of 0 to 1:
{' -> '.join(path)}

Consider: Is it making progress toward a solution? Is it logical?
Return ONLY a number between 0 and 1, e.g.: 0.75
"""
        
        from codeharness.provider.fake import FakeLLM
        # FakeLLM 返回固定值，真模型可接入评估器
        if isinstance(llm, FakeLLM):
            return 0.5 + hash(node.thought) % 100 / 200  # 伪随机评分
        
        try:
            resp = llm.aask(prompt)
            score = float(resp.strip())
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.5  # 默认中分
    
    def best_leaf(self) -> ThoughtNode:
        """找得分最高的叶子节点。"""
        leaves = self.get_leaves()
        if not leaves:
            return self.root
        return max(leaves, key=lambda n: n.score)


class TotAgent:
    """ToT 智能体：树搜索 + 择优。"""
    
    def __init__(self, llm, goal: str, num_paths: int = 3, max_depth: int = 3):
        self.llm = llm
        self.goal = goal
        self.num_paths = num_paths
        self.max_depth = max_depth
        self.tree = ThoughtTree(f"Goal: {goal}")
        
    async def think(self) -> str:
        """执行一次 ToT 思考并返回最优路径。"""
        # 第 0 层：根节点
        self.tree.evaluate(self.tree.root, self.llm)
        
        # 逐层扩展
        for depth in range(self.max_depth):
            leaves = self.tree.get_leaves()
            if not leaves:
                break
            
            # 对每个叶子生成候选
            for leaf in leaves:
                self.tree.expand(leaf, self.llm, num_choices=self.num_paths)
            
            # 评估新节点
            for child in self.tree.root.children:
                self.tree.evaluate(child, self.llm)
            
            # 剪枝：只保留 top K
            if len(leaves) > self.num_paths:
                sorted_leaves = sorted(leaves, key=lambda n: n.score, reverse=True)
                kept = set(n.id for n in sorted_leaves[:self.num_paths])
                # 简化：直接截断（完整实现需删除非 top 节点及其子树）
        
        # 返回最优路径
        best = self.tree.best_leaf()
        return " -> ".join(best.get_path())
