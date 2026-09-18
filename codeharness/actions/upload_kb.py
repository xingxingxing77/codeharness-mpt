"""B7: 知识库摄取入口——上传文档到 kb 切片。

对照 2 §结论 -2：qdrant_store.py:doc_type="kb" 有 schema 无写入者，本件补全上半截。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from codeharness.base.action import Action
from codeharness.document_store.qdrant_store import QdrantStore, Point
from codeharness.provider.gateway import LLMGateway
from codeharness.runtime import CURRENT_USER


class UploadKB(Action):
    """上传文档到知识库。"""

    name: str = "upload_kb"
    
    async def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """上传文件列表到 kb 切片。

        Args:
            params: {
                "files": list[str],      # 文件路径列表（相对会话目录）
                "doc_type": str="kb"     # 文档类型（默认 kb）
            }

        Returns:
            {
                "uploaded_count": int,   # 成功上传数量
                "errors": list[str]      # 错误列表
            }
        """
        files = params.get("files", [])
        doc_type = params.get("doc_type", "kb")
        
        if not files:
            return {"uploaded_count": 0, "errors": ["files 为空"]}
        
        errors = []
        uploaded = 0
        
        # 初始化存储
        store = QdrantStore()
        gateway = LLMGateway(cfg=None, cost_manager=None)
        embeddings = gateway.embeddings()
        
        # 逐文件读取并嵌入
        for filepath in files:
            try:
                # 读取文件内容
                file_path = Path(filepath)
                if not file_path.exists():
                    errors.append(f"文件不存在：{filepath}")
                    continue
                
                content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
                
                # 嵌入
                dense = await embeddings.aembed_query(content)
                
                # 构造点
                user_id = CURRENT_USER.get("default")
                point = Point(
                    id=f"{doc_type}:{user_id}:upload:{uploaded}",
                    text=content,
                    dense=list(dense),
                    doc_type=doc_type,
                    user_id=user_id
                )
                
                # 写入
                await store.write([point])
                uploaded += 1
                
            except Exception as e:
                errors.append(f"{filepath}: {type(e).__name__}: {e}")
        
        return {
            "uploaded_count": uploaded,
            "errors": errors
        }
