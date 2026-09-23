"""R9 · Qdrant 检索层。判定 `重`（源 `document_store/{base_store,qdrant_store}.py` 176 行不复制）：
源是单 dense 向量 + `k=10`、`write()` 空 stub、裸 `except:` ×2、还在用废弃的 `recreate_collection`。

本件的四条现代形态（都在 server 1.18.2 / client 1.19.0 上实测过）：
  1. **named vectors**：一条 record 同时有 `dense`(语义) 与 `sparse`(词法)，
     召回靠服务端 `Fusion.RRF` 融合，而不是自己把两路结果拼起来；
  2. **单 collection + payload 多租户**：`user_id`/`session_id`/`project`/`doc_type` 进 payload 做 filter，
     取代源和旧实现里"每租户/每项目一 collection"（collection 数量会随租户线性膨胀，索引和备份都遭不住）；
     `user_id` 建 `is_tenant=True` 的 keyword 索引，让租户内检索走本地快路径；
  3. **INT8 标量量化**降内存，原始向量仍在（`always_ram=False`），要回退就把量化配置去掉；
  4. 客户端 1.19 的 `VectorParams` **不支持** per-vector quantization，量化只有 collection 级——
     所以 dense/sparse 都在这一个 collection 里，量化对全表生效。
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from qdrant_client import AsyncQdrantClient, models as m

from codeharness.configs.settings import settings

DENSE = "dense"
SPARSE = "sparse"
SPARSE_SPACE = 1 << 20
# 词法切分：英文数字连成词，中文按单字。够用且跨进程稳定——不用 str.hash()（它按进程加盐）
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def sparse_from_text(text: str) -> m.SparseVector:
    """词法稀疏向量：crc32(词) 当维度，值是 sublinear tf(1+log n)。

    ponytail: 没有 idf/语料统计，常见词会贡献噪声（服务端 Modifier.IDF 只补一半）；
    升级路径 = 换 bge-m3 的 sparse 权重输出，届时只改这一个函数。
    """
    counts = Counter(TOKEN_RE.findall(text.lower()))
    terms: dict[int, float] = {}
    for w, n in counts.items():
        idx = zlib.crc32(w.encode("utf-8")) % SPARSE_SPACE
        terms[idx] = max(terms.get(idx, 0.0), 1.0 + math.log(n))
    idxs = sorted(terms)
    return m.SparseVector(indices=idxs, values=[terms[i] for i in idxs])


@dataclass
class Point:
    """一条可检索记录：文本自带词法向量，语义向量由调用方（embeddings）算好传入。"""

    id: str
    text: str
    dense: list[float]
    doc_type: str                      # kb | exp | memory
    user_id: str = "default"
    session_id: str = ""
    project: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def payload(self) -> dict[str, Any]:
        return {"text": self.text, "doc_type": self.doc_type, "user_id": self.user_id,
                "session_id": self.session_id, "project": self.project, **self.extra}


class QdrantStore:
    def __init__(self, collection: str = "", url: str = "", client=None):
        # 单 collection：名字取 QDRANT__COLLECTION_PREFIX（该字段此前零读者，现在是这里的唯一出口）
        self.collection = collection or settings.qdrant.collection_prefix or "codeharness"
        # check_compatibility=False：qdrant 不在时其版本探测线程（_check_compatibility，非 daemon）
        # 会无限重试吊住进程退出——门禁「不挂在外部服务上」的硬要求（s3b t14 卡退实测现形）。
        self.client = client or AsyncQdrantClient(url=url or settings.qdrant.url,
                                                  api_key=settings.qdrant.api_key or None,
                                                  check_compatibility=False)
        self._ready = False

    async def ensure(self, dim: int) -> None:
        """建集合（幂等）。dim 取实际 embedding 输出维度，bge-m3 是 1024。"""
        if self._ready:
            return
        if await self.client.collection_exists(self.collection):
            # C21：老集合是在 `source` 进 payload 之前建的。不在这里补那一道索引，「按 source 过滤 /
            # 下架单份文档」就退化成全扫——`ensure()` 每进程只走到一次，补它的市场价是一次 API 调用。
            await self._ensure_indexes()
            self._ready = True
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={DENSE: m.VectorParams(size=dim, distance=m.Distance.COSINE)},
            sparse_vectors_config={SPARSE: m.SparseVectorParams(modifier=m.Modifier.IDF)},
            quantization_config={"scalar": {"type": "int8", "always_ram": False}},
        )
        await self._ensure_indexes()
        self._ready = True

    async def _ensure_indexes(self) -> None:
        """payload 上的 keyword 索引（`create_payload_index` 幂等，重复调没事）。

        `source`（C21：切片来自哪份文件）与 `page` 里只给 `source` 建索引——按页筛今天没人做，
        而 `.pdf` 的 page 是整数，keyword 索引要的是字符串，硬塞进去就是一道用不上的写放大。"""
        for key, tenant in (("user_id", True), ("session_id", False), ("project", False),
                            ("doc_type", False), ("source", False)):
            await self.client.create_payload_index(
                self.collection, field_name=key,
                field_schema=m.KeywordIndexParams(type="keyword", is_tenant=tenant or None))

    async def write(self, points: Sequence[Point]) -> int:
        if not points:
            return 0
        await self.ensure(len(points[0].dense))
        await self.client.upsert(self.collection, points=[
            m.PointStruct(id=p.id, vector={DENSE: p.dense, SPARSE: sparse_from_text(p.text)},
                          payload=p.payload) for p in points])
        return len(points)

    @staticmethod
    def _filters(doc_type: str, user_id: str, **scope) -> m.Filter | None:
        pairs = [("doc_type", doc_type), ("user_id", user_id), *scope.items()]
        must = [m.FieldCondition(key=k, match=m.MatchValue(value=v)) for k, v in pairs if v]
        return m.Filter(must=must) if must else None

    async def search(self, query: str, dense: list[float], *, k: int = 10, hybrid: bool = True,
                     doc_type: str = "", user_id: str = "default", **scope) -> list[m.ScoredPoint]:
        """hybrid=True 走服务端 RRF 融合两路召回；False 只走 dense —— S5 门禁要的就是这两个数的对比。"""
        flt = self._filters(doc_type, user_id, **scope)
        if not hybrid:
            return (await self.client.query_points(
                self.collection, query=dense, using=DENSE, limit=k, query_filter=flt)).points
        res = await self.client.query_points(
            self.collection,
            prefetch=[m.Prefetch(query=dense, using=DENSE, limit=k),
                      m.Prefetch(query=sparse_from_text(query), using=SPARSE, limit=k)],
            query=m.FusionQuery(fusion=m.Fusion.RRF), limit=k, query_filter=flt)
        return list(res.points)

    async def delete_scope(self, *, doc_type: str = "", user_id: str = "default", **scope) -> None:
        await self.client.delete(self.collection, points_selector=m.FilterSelector(
            filter=self._filters(doc_type, user_id, **scope)), wait=True)

    # B7 那轮在这里加过一个 `aembed_documents`（自己建 embedding、自己拼 Point、自己 write），
    # 与 `actions/upload_kb.py` 是**同一件事的两个半截实现**，且它零生产调用者、embeddings 写死在
    # 函数体里没法注入门禁替身。C3 把摄取接成真链路时删掉它：入库只有一条路（UploadKB）。

    async def drop(self) -> None:
        """整表删除：只给自测与租户注销用。"""
        if await self.client.collection_exists(self.collection):
            await self.client.delete_collection(self.collection)
        self._ready = False
