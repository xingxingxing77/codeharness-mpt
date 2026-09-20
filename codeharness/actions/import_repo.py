"""仓库导入：用 graph_repository 三件套存 SPO，生成 `.json` 与 `.mmd` 文本。

判 `改`：源 `import_repo.py`(203) + `extract_readme.py`(108) 
重命名为 N2 示例件（不挂角色，只走 ext_api），装配在 ext_api、不进角色表。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx

from codeharness.base.action import Action
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.schema import Document
from codeharness.utils.di_graph_repository import DiGraphRepository


class ImportRepo(Action):
    """导入仓库结构并建立关系图。

    ponytail: 天花板是「graph_repository schema 变更即解析失败」，load_json 有异常处理。
    """

    name: str = "import_repo"
    
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._artifact_store = ArtifactStore()

    async def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """导入仓库并生成关系图。

        Args:
            params: {
                "repo_path": str,          # 仓库根目录路径
                "save_name": str="repo",   # 保存文件名（不含扩展名）
                "include_files": bool=True # 是否包含文件节点
            }

        Returns:
            {
                "node_count": int,         # 节点数
                "edge_count": int,         # 边数
                "saved_json": str|None,    # JSON 落盘路径
                "saved_mmd": str|None      # Mermaid 落盘路径
            }
        """
        repo_path = params.get("repo_path", ".")
        save_name = params.get("save_name", "repo")
        include_files = params.get("include_files", True)

        # save_name 拼成 `{会话根}/{save_name}.json` 交给 load_from，`../` = 越界读任意 .json
        # （读进来的图会并进本次产物）。写侧因下面 save 显式传会话根才没越界。
        # HTTP 端点已拦一次（400），这里不依赖调用方守规矩：ext_api 装配可能有第二个调用者。
        if not save_name or Path(save_name).name != save_name:
            raise ValueError(f"非法 save_name {save_name!r}：不能包含路径分隔")

        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            return {"error": f"repository path does not exist: {repo_path}"}

        # 创建或加载 graph repository
        repo = await DiGraphRepository.load_from(
            pathname=Path(self._artifact_store.root) / f"{save_name}.json"
        )

        # 扫描仓库结构
        node_set = set()
        
        # 添加根节点
        root_node = str(repo_path)
        node_set.add(root_node)
        
        # 递归扫描目录和文件
        for item in repo_path.rglob("*"):
            if item.is_dir():
                node_set.add(str(item))
            elif item.is_file() and include_files:
                node_set.add(str(item))
        
        # 构建父子关系
        for node in node_set:
            node_path = Path(node)
            parent = node_path.parent
            if str(parent) != str(repo_path) and str(parent) in node_set:
                await repo.insert(subject=str(parent), predicate="contains", object_=node)
            
            # 文件类型关系
            if node_path.is_file():
                suffix = node_path.suffix.lower()
                await repo.insert(
                    subject=node,
                    predicate="has_type",
                    object_=f"file:{suffix[1:]}"  # 去掉点
                )

        # 统计
        node_count = len(node_set)
        edge_count = sum(1 for _ in repo.repo.edges())

        # 保存为 JSON
        saved_json = None
        await repo.save(path=Path(self._artifact_store.root))
        saved_json = str(repo.pathname)

        # 生成 Mermaid 序列图（保存到 docs 子目录）
        mmd_content = self._generate_mmd(repo.repo)
        saved_mmd = None
        doc = Document(filename=f"{save_name}.mmd", content=mmd_content)
        await self._artifact_store.save(subdir="docs", doc=doc)
        saved_mmd = str(Path(self._artifact_store.root) / "docs" / f"{save_name}.mmd")

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "saved_json": saved_json,
            "saved_mmd": saved_mmd
        }

    def _generate_mmd(self, graph: networkx.DiGraph) -> str:
        """生成 Mermaid 关系图。

        ponytail: ceiling 是「大图时浏览器渲染卡顿」，前端应支持按需展开。
        """
        lines = ["graph TD"]
        
        # 收集所有节点
        nodes = set()
        for s, o, p in graph.edges(data="predicate"):
            nodes.add(s)
            nodes.add(o)
        
        # 生成节点定义
        for node in nodes:
            # 简化节点名称（取最后一部分）
            short_name = Path(node).name if "/" in node or "\\" in node else node
            # 转义特殊字符
            safe_name = short_name.replace("'", "\\'").replace('"', '\\"')
            lines.append(f"    {safe_name}[\"{short_name}\"]")
        
        # 生成边
        for s, o, p in graph.edges(data="predicate"):
            short_s = Path(s).name if "/" in s or "\\" in s else s
            short_o = Path(o).name if "/" in o or "\\" in o else o
            safe_s = short_s.replace("'", "\\'").replace('"', '\\"')
            safe_o = short_o.replace("'", "\\'").replace('"', '\\"')
            lines.append(f"    {safe_s} -->|{p}| {safe_o}")
        
        return "\n".join(lines) + "\n"
