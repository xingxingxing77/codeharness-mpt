"""RebuildClassView。= 源 actions/rebuild_class_view.py(235) 的 `改`：
「AST 文件面 + pyreverse 类面 → SPO 图 → mermaid 类图」的流水线逐段照源
（_diff_path/_align_root/_create_mermaid_class/_create_mermaid_relationship）；
上下层换成新栈：ProjectRepo/git_repo.workdir → ArtifactStore 会话根（graph 落 docs/graph_repo/、
.mmd 落 resources/data_api_design/）；aiofiles 直写改 asyncio.to_thread（本仓零 aiofiles 依赖）。
输入：instruct_content={"repo_path": 目录} 或 content=目录路径；缺省对会话 src/ 建图。
"""
import asyncio
from pathlib import Path
from typing import Optional, Set, Tuple

from codeharness.base.action import BaseAction
from codeharness.const import (AGGREGATION, COMPOSITION, DATA_API_DESIGN_FILE_REPO, GENERALIZATION,
                               GRAPH_REPO_FILE_REPO, RepoName)
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.repo_parser import DotClassInfo, RepoParser
from codeharness.schema import Message, UMLClassView
from codeharness.utils.common import concat_namespace, split_namespace
from codeharness.utils.di_graph_repository import DiGraphRepository
from codeharness.utils.graph_repository import GraphKeyword, GraphRepository


class RebuildClassView(BaseAction):
    """把源码目录的类关系写进 SPO 图并产出 mermaid 类图（前端 N6 与 RAG 的共同数据源）。"""

    graph_db: Optional[object] = None    # DiGraphRepository（仓内约定：非 pydantic 件挂 object，同 BaseAction.llm）

    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        target = (msg.instruct_content or {}).get("repo_path") or msg.content or str(store.root / RepoName.SRC)
        target = Path(target)
        graph_path = store.root / GRAPH_REPO_FILE_REPO / "class_view"
        self.graph_db = await DiGraphRepository.load_from(str(graph_path.with_suffix(".json")))
        rp = RepoParser(base_directory=target)
        class_views, relationship_views, package_root = await rp.rebuild_class_views(path=target)
        await GraphRepository.update_graph_db_with_class_views(self.graph_db, class_views)
        await GraphRepository.update_graph_db_with_class_relationship_views(self.graph_db, relationship_views)
        await GraphRepository.rebuild_composition_relationship(self.graph_db)
        # ast 文件面（源 :57-62：与 class_views 对齐同一根再注入）
        direction, diff_path = self._diff_path(path_root=target.resolve(), package_root=Path(package_root))
        for file_info in rp.generate_symbols():
            file_info.file = self._align_root(file_info.file, direction, diff_path)
            await GraphRepository.update_graph_db_with_file_info(self.graph_db, file_info)
        filename = await self._create_mermaid_class_views(store)
        await self.graph_db.save(path=store.root / GRAPH_REPO_FILE_REPO)
        n_cls = len({r.subject for r in await self.graph_db.select(predicate=GraphKeyword.IS,
                                                                    object_=GraphKeyword.CLASS)})
        out = (f"类图重建完成: {n_cls} 类 -> {filename.name}")
        return Message(content=out, role="assistant", cause_by=self.name, sent_from="Engineer",
                       instruct_content={"mmd_filename": str(filename.relative_to(store.root)),
                                         "class_count": n_cls},
                       instruct_schema="RebuildClassViewOutput")

    async def _create_mermaid_class_views(self, store: ArtifactStore) -> Path:
        """图源 = graph_db；产出 resources/data_api_design/class_view.class_diagram.mmd（方案 C：
        .mmd 落盘为真源，渲染在前端）。源的 hasMermaidClassDiagramFile 记账照留。"""
        path = store.root / DATA_API_DESIGN_FILE_REPO
        path.mkdir(parents=True, exist_ok=True)
        filename = path / "class_view.class_diagram.mmd"
        content = "classDiagram\n"
        class_distinct, relationship_distinct = set(), set()
        rows = await self.graph_db.select(predicate=GraphKeyword.IS, object_=GraphKeyword.CLASS)
        for r in rows:
            body = await self._create_mermaid_class(r.subject)
            if body:
                content += body
                class_distinct.add(r.subject)
        for r in rows:
            body, distinct = await self._create_mermaid_relationship(r.subject)
            if body:
                content += body
                relationship_distinct.update(distinct)
        logger.info(f"classes: {len(class_distinct)}, relationship: {len(relationship_distinct)}")
        await asyncio.to_thread(filename.write_text, content, encoding="utf-8")
        return filename

    async def _create_mermaid_class(self, ns_class_name: str) -> str:
        fields = split_namespace(ns_class_name)
        if len(fields) > 2:
            return ""                                   # 子包类忽略（源同款）
        rows = await self.graph_db.select(subject=ns_class_name, predicate=GraphKeyword.HAS_DETAIL)
        if not rows:
            return ""
        dot_class_info = DotClassInfo.model_validate_json(rows[0].object_)
        class_view = UMLClassView.load_dot_class_info(dot_class_info)
        await self.graph_db.insert(ns_class_name, GraphKeyword.HAS_CLASS_VIEW, class_view.model_dump_json())
        for c in dot_class_info.compositions:
            await self.graph_db.insert(subject=ns_class_name, predicate=GraphKeyword.IS + COMPOSITION + GraphKeyword.OF,
                                       object_=concat_namespace("?", c))
        for a in dot_class_info.aggregations:
            await self.graph_db.insert(subject=ns_class_name, predicate=GraphKeyword.IS + AGGREGATION + GraphKeyword.OF,
                                       object_=concat_namespace("?", a))
        return class_view.get_mermaid(align=1)

    async def _create_mermaid_relationship(self, ns_class_name: str) -> Tuple[Optional[str], Optional[Set]]:
        s_fields = split_namespace(ns_class_name)
        if len(s_fields) > 2:                           # split_namespace 默认 maxsplit=1 → [包, 类]；>2 是子包，忽略
            return None, None
        predicates = {GraphKeyword.IS + v + GraphKeyword.OF: v for v in [GENERALIZATION, COMPOSITION, AGGREGATION]}
        mappings = {GENERALIZATION: " <|-- ", COMPOSITION: " *-- ", AGGREGATION: " o-- "}
        content = ""
        distinct = set()
        for p, v in predicates.items():
            rows = await self.graph_db.select(subject=ns_class_name, predicate=p)
            for r in rows:
                o_fields = split_namespace(r.object_)
                if len(o_fields) > 2:
                    continue
                relationship = mappings.get(v, " .. ")
                link = f"{o_fields[1]}{relationship}{s_fields[1]}"   # 源用短类名连边（对齐同一根后）
                distinct.add(link)
                content += f"\t{link}\n"
        return content, distinct

    @staticmethod
    def _diff_path(path_root: Path, package_root: Path) -> Tuple[str, str]:
        if len(str(path_root)) > len(str(package_root)):
            return "+", str(path_root.relative_to(package_root))
        if len(str(path_root)) < len(str(package_root)):
            return "-", str(package_root.relative_to(path_root))
        return "=", "."

    @staticmethod
    def _align_root(path: str, direction: str, diff_path: str) -> str:
        if direction == "=":
            return path
        if direction == "+":
            return diff_path + "/" + path
        return path[len(diff_path) + 1:]
