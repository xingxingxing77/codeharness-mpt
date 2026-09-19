"""ToT 数据结构：源 strategy/base.py(109) 的 `改`移植。

源用 anytree 的 `Node`/`RenderTree` 做树。本仓不引 anytree（新栈约束：不新增依赖），
用纯 Python 父子指针实现同语义的 `ThoughtNode`/`ThoughtTree`——字段逐字照源
（name/value/id/valid_status），方法对齐（all_nodes/update_node/parse_node_path/show）。
`value` 是累计分（源 evaluate_node 里 `parent_value + value`），不是单点分。
"""
from __future__ import annotations

from typing import List, Optional


class ThoughtNode:
    """思考树的一个节点。对齐源 base.py:33-46（name/value/id/valid_status + update_* 两法）。"""

    def __init__(self, name: str = "", parent: Optional["ThoughtNode"] = None, id: int = 0):
        self.name = name
        self.parent = parent
        self.children: List["ThoughtNode"] = []
        self.value: float = 0            # 累计分（含父链）
        self.id = id
        self.valid_status: bool = True
        if parent is not None:
            parent.children.append(self)

    def update_value(self, value: float) -> None:
        self.value = value

    def update_valid_status(self, status: bool) -> None:
        self.valid_status = status


class ThoughtTree:
    """以 root 为锚的思考树。对齐源 base.py:49-108 的 all_nodes/update_node/parse_node_path/show。"""

    def __init__(self, root: ThoughtNode):
        self.root = root

    def _walk(self, node: ThoughtNode, depth: int = 0):
        """前序遍历，yield (prefix, node)。prefix 用于 show 缩进。"""
        yield ("  " * depth, node)
        for child in node.children:
            yield from self._walk(child, depth + 1)

    @property
    def all_nodes(self) -> List[ThoughtNode]:
        return [node for _, node in self._walk(self.root)]

    def update_node(self, thought: List[dict], current_node: Optional[ThoughtNode] = None) -> List[ThoughtNode]:
        """按 [{node_id, node_state_instruction}] 在 current_node 下挂子节点。对齐源 update_node。"""
        current_node = current_node or self.root
        nodes = []
        for info in thought:
            node = ThoughtNode(name=str(info.get("node_state_instruction", "")),
                               parent=current_node, id=int(info.get("node_id", 0)))
            nodes.append(node)
        return nodes

    def parse_node_path(self, node: Optional[ThoughtNode]) -> List[str]:
        """根→node 的名字路径。对齐源 parse_node_path。"""
        path: List[str] = []
        while node is not None:
            path.append(node.name)
            node = node.parent
        path.reverse()
        return path

    def show(self) -> None:
        print("\nUpdated Tree:")
        for prefix, node in self._walk(self.root):
            print(f"{prefix}{node.name}, value: {node.value}, valid_status: {node.valid_status}")
