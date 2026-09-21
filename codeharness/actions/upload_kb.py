"""C3: 知识库摄取——把文档切成切片，写进 `doc_type="kb"` 那条切片。

对照2 §结论-2 记的「`doc_type="kb"` 有 schema 无写入者」到这里通电：唯一生产调用点是
`server/api/workspace.py::upload_kb`（multipart 上传 → 落会话工作区 → 本件切块入库），
读者在 `roles/role_zero.py`（`RoleZero.kb`，每次 think 前按任务召回）。

B7 那轮的写法有三处「写好了没通电」的根因，这一版逐条对上：
- embeddings/store 硬写在方法体里 → 门禁换不了替身，也就没法端到端测 ⇒ 改成构造期可注入、缺省现取；
- 一个文件一个点、id 里带上传计数器 → 重传同一份 FAQ 就多一套切片，切片还会随次序漂移
  ⇒ id 走 `memory.longterm.point_id`（内容派生，重传幂等，与记忆入库同一条规则）；
- 整个文件压成一个向量 → 长文档检索粒度等于没有 ⇒ 走 `document.IndexableDocument`
  （.txt/.md 按 256 字符切块、.pdf 按页）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from codeharness.base.action import Action
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.memory.longterm import point_id
from codeharness.provider.gateway import LLMGateway
from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER

SUPPORTED = {".txt", ".md", ".docx", ".pdf"}     # 唯一出口：端点上传的白名单也读这个集合


def _texts_of(path: Path) -> list[str]:
    """一份文档 → 切片文本。后缀在**读它之前**就判，别拿 pandas 的
    「Content column not found in DataFrame.」当用户看得懂的拒绝理由。"""
    if not path.exists():
        raise FileNotFoundError("文件不存在")
    if path.suffix.lower() not in SUPPORTED:
        raise NotImplementedError(
            f"知识库只摄取文本类文档（{'/'.join(sorted(SUPPORTED))}），收到 {path.suffix or '无后缀'}")
    from codeharness.document import IndexableDocument
    doc = IndexableDocument.from_path(path)
    if not isinstance(doc.data, list):        # DataFrame（.csv/.json/.xlsx）没有「一段文本」的语义
        raise NotImplementedError(f"{path.suffix} 读出来是表格，没有可切片的文本")
    texts, _ = doc.get_docs_and_metadatas()
    return [t for t in texts if t and t.strip()]


class UploadKB(Action):
    """把文件切块灌进知识库切片。`store`/`embeddings` 是门禁注入口，生产留 None 现取。"""

    name: str = "upload_kb"
    store: Optional[Any] = None
    embeddings: Optional[Any] = None

    async def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """上传文档到知识库切片。

        Args:
            params: {"files": list[str] 文件路径, "doc_type": str="kb"}

        Returns:
            {"uploaded_count": 写入点数（按内容去重后）, "chunk_count": 切出的切片数,
             "errors": list[str] 逐文件的失败原因（不中断其余文件）}
        """
        files = params.get("files", [])
        doc_type = params.get("doc_type", "kb")
        if not files:
            return {"uploaded_count": 0, "chunk_count": 0, "errors": ["files 为空"]}

        store = self.store or QdrantStore()
        embeddings = self.embeddings or LLMGateway.embeddings()     # 静态工厂（向量模型不走 gateway 实例）
        user_id = CURRENT_USER.get() or "default"
        scope = f"{doc_type}/{user_id}/{CURRENT_PROJECT.get()}"

        errors: list[str] = []
        chunks: list[str] = []
        for filepath in files:
            try:
                found = await asyncio.to_thread(_texts_of, Path(filepath))
            except Exception as e:
                errors.append(f"{Path(filepath).name}: {type(e).__name__}: {e}")
                continue
            if not found:
                errors.append(f"{Path(filepath).name}: 没读出可切片的文本")
            chunks += found

        if not chunks:
            return {"uploaded_count": 0, "chunk_count": 0, "errors": errors}
        vectors = await embeddings.aembed_documents(chunks)
        points = [Point(id=point_id(scope, text), text=text, dense=list(vec), doc_type=doc_type,
                        user_id=user_id, project=CURRENT_PROJECT.get())
                  for text, vec in zip(chunks, vectors) if vec]
        written = await store.write(points)
        return {"uploaded_count": written, "chunk_count": len(chunks), "errors": errors}
