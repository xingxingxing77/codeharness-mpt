"""联网问答：接工具注册表的搜索引擎（ddg/serper），走沙箱隔离；输出进 ArtifactStore。

判 `改`：源 `search_enhanced_qa.py`(168) + `search_engine_serper.py`(119) + `search_engine_ddg.py`(94) 
已收敛成 `tools.search_engine.engine()`，这里只装配 gateway+ArtifactStore。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codeharness.base.action import Action
from codeharness.configs.settings import settings
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.schema import Document
from codeharness.tools.search_engine import search


class SearchEnhancedQA(Action):
    """联网搜索并总结。

    ponytail: 天花板是「端点改版即解析不到」，DDG 路径用 requests+bs4 直取 HTML 结果块。
    """

    name: str = "search_enhanced_qa"
    
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._artifact_store = ArtifactStore()

    async def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """执行联网搜索并返回结果。

        Args:
            params: {
                "query": str,              # 搜索查询
                "max_results": int=8,      # 最多返回结果数
                "save_to": str|None=None   # 落盘文件名（相对会话目录）
            }

        Returns:
            {
                "results": list[dict],     # [{title, link, snippet}]
                "summary": str             # 简单文本总结
            }
        """
        query = params.get("query", "")
        max_results = params.get("max_results", 8)
        
        if not query:
            return {"results": [], "summary": "", "error": "query is required"}

        # 调用搜索引擎
        results = await search(query, max_results)
        
        # 生成简单总结
        summary_lines = [f"搜索到 {len(results)} 条结果："]
        for i, r in enumerate(results, 1):
            summary_lines.append(f"{i}. {r['title']}")
            summary_lines.append(f"   {r['snippet'][:100]}...")
        
        summary = "\n".join(summary_lines)
        
        # 落盘到 ArtifactStore（走 docs 子目录）
        save_to = params.get("save_to")
        if save_to:
            artifact_data = {
                "query": query,
                "results": results,
                "summary": summary,
                "settings": {
                    "max_results": max_results,
                    "engine": settings.search.engine if hasattr(settings.search, 'engine') else "auto"
                }
            }
            doc = Document(filename=save_to, content=json.dumps(artifact_data, ensure_ascii=False, indent=2))
            await self._artifact_store.save(subdir="docs", doc=doc)
        
        return {
            "results": results,
            "summary": summary,
            "saved_to": save_to
        }
