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
from codeharness.document_store.embed_split import split_for_embedding
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.memory.longterm import point_id
from codeharness.provider.gateway import LLMGateway
from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER

SUPPORTED = {".txt", ".md", ".docx", ".pdf"}     # 唯一出口：端点上传的白名单也读这个集合


class KbFormatError(ValueError):
    """门口就该拒的那两类（不收 / 本机读不了）。单独一个类型是给 `_call` 分派用的：
    它 catch 到这一类就**只印文案**，不再往前面拼 `type(e).__name__`——
    `ModuleNotFoundError: No module named 'docx2txt'` 那种给用户看的东西就是这么拼出来的（C26）。"""


def door_refusal(suffix: str) -> str:
    """门口那一句人话。两种拒必须分开说：不收（换格式）vs 本机读不了（补组件）。
    刻意不含 `ModuleNotFoundError`/`ImportError` 那种 Python 原文——那正是 C26 量到的坏形状。"""
    from codeharness.document import OPTIONAL_READERS, reader_available
    if suffix not in SUPPORTED:
        return (f"知识库只摄取文本类文档（{'/'.join(sorted(SUPPORTED))}），收到 {suffix or '无后缀'}")
    if reader_available(suffix):
        return ""
    mod = OPTIONAL_READERS.get(suffix, "?")
    return (f"{suffix} 这台机器暂时读不了：缺读取组件 `{mod}`（补法 `pip install {mod}`）"
            f"——这份文档**没有入库**，也没落进 kb/，补好组件再传一次")


def _texts_of(path: Path) -> list[tuple[str, dict]]:
    """一份文档 → `[(切片文本, 这一片的 metadata)]`。

    后缀在**读它之前**就判，别拿 pandas 的「Content column not found in DataFrame.」当用户看得懂的拒绝理由。
    C21：`get_docs_and_metadatas()` 本来就把 `(docs, metadatas)` 两值都给出来（`TextLoader` 的每片带
    `{"source": 路径}`、`PyPDFLoader` 每片带 `{"source","page"}`），而这里从前写的是 `texts, _ = …`
    ——**第二个返回值就地丢弃**，于是召回说不出这段是哪份文件、也删不掉单份文档。"""
    if not path.exists():
        raise FileNotFoundError("文件不存在")
    suffix = path.suffix.lower()
    if reason := door_refusal(suffix):
        raise KbFormatError(reason)
    from codeharness.document import IndexableDocument
    doc = IndexableDocument.from_path(path)
    if not isinstance(doc.data, list):        # DataFrame（.csv/.json/.xlsx）没有「一段文本」的语义
        raise KbFormatError(f"{suffix} 读出来是表格，没有可切片的文本")
    texts, metas = doc.get_docs_and_metadatas()
    return [(t, m or {}) for t, m in zip(texts, metas) if t and t.strip()]


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
        slices: list[tuple[str, dict]] = []
        for filepath in files:
            try:
                found = await asyncio.to_thread(_texts_of, Path(filepath))
            except KbFormatError as e:
                errors.append(f"{Path(filepath).name}: {e}")            # 门口那类拒：只给人话，不拼类名（C26）
                continue
            except Exception as e:
                errors.append(f"{Path(filepath).name}: {type(e).__name__}: {e}")
                continue
            if not found:
                errors.append(f"{Path(filepath).name}: 没读出可切片的文本")
            slices += found

        if not slices:
            return {"uploaded_count": 0, "chunk_count": 0, "errors": errors}
        # C27 + C21 的接缝：**逐片过出口**，块和它的来源一起往下走（整批一次过就把这个对应关系洗掉了）。
        # 上游那个 256 的切块器只在有换行处生效，`.docx` 整篇一块、`.pdf` 一页一块且不看长度——
        # 不过这一道，超长切片的尾巴会被端点静默截掉（读数见 `document_store/embed_split.py` 模块头）。
        # `chunk_count` 报**切完之后**的数：它回答的是「库里有多少个可检索切片」，不是「读出来几段」。
        pairs = [(c, meta) for text, meta in slices for c in split_for_embedding([text])]
        vectors = await embeddings.aembed_documents([c for c, _ in pairs])
        points = []
        for (text, meta), vec in zip(pairs, vectors):
            if not vec:
                continue
            # C21：出处只记**文件名**。`TextLoader`/`PyPDFLoader` 给的 `source` 是服务端绝对路径
            # （本机实测 `C:\Users\…\Temp\tmpxxxx\b.md`），原样进 payload 就等于把服务器目录结构顺着
            # 召回结果漏到界面上；而端点本来就按 basename 落进 `kb/`（`workspace.py` 门口剥过一层），
            # 所以文件名既是稳定键也是全部有意义的信息。
            src = Path(str(meta.get("source") or "")).name
            # C21：点 id 的派生式**带上 source**。只由内容派生时，同一句话出现在两份文档里会算出
            # 同一个点——后写的把先写的**连出处一起顶掉**，于是「只下架这一份」要么带走别份的切片、
            # 要么留下一条 attribution 已错的僵尸点（与 C4 那条「租户必须在派生里」同族）。
            # 代价照 C4/C12/C27 先例写在头里：改造前的老点没有 source、与新点**并存**，
            # 且按 `source` 过滤删不到它们（读侧按 payload 走，不看 id）；dev 不迁移清洗。
            extra = {"source": src}
            if meta.get("page") is not None:
                extra["page"] = meta["page"]
            points.append(Point(id=point_id(f"{scope}/{src}", text), text=text, dense=list(vec),
                                doc_type=doc_type, user_id=user_id, project=CURRENT_PROJECT.get(),
                                extra=extra))
        written = await store.write(points)
        return {"uploaded_count": written, "chunk_count": len(pairs), "errors": errors}
