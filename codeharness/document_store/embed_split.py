"""C27 · embedding 输入的唯一出口：任何要进向量端的文本，先切成 ≤ H 的块。

**为什么需要这么一件**（两个读数与出处见 `configs/settings.py` 里
`EMBEDDING_OBSERVED_TRUNCATION_CHARS` 那条注释）：OpenAI 兼容的 embedding 端点会把超长输入
**静默截断**——本机 bge-m3 在真中文文档上量到「全文向量与前 3200 字的向量逐维完全相同」，
也就是尾巴一个字都没进向量，而它不报错、不返回任何标记。

改前四条入库路里有三条把整串文本直接发出去：`.txt/.md` 那个切块器（`document.py:47` 的
`chunk_size=256`）**只在有换行的地方生效**（实测 900 字不换行的段落原样出一块，langchain 自己打
警告 `Created a chunk of size 400, which is longer than the specified 256`）；`.pdf` 按页、
`.docx` 整篇一块；最坏的是默认开着的记忆腿——`memory/longterm.py` 的 `overflow()` 把整条消息当
一个点，而 `read_file` 单次可回 2 万字符、`_compress` 原样灌进库。症状看着像「模型笨」而不是
「索引缺」，因为词法腿（`qdrant_store.sparse_from_text`）是本地从**全文**算的、不经端点：
按原文关键词问得到，换个说法问不到。

**不在本件里的**（口径已在 PLAN C27 定死，别顺手扩）：overlap、块大小往哪调、语义切/结构化切/
父子块——那些是调优，等 C20 那把有判别力的检索基线尺子出来再谈。本件只管正确性：
不许有一段文本静默进不了向量。
"""
from __future__ import annotations

from typing import Sequence

from codeharness.configs.settings import settings


def split_for_embedding(texts: Sequence[str], max_chars: int = 0) -> list[str]:
    """一批文本 → 逐块 ≤ H 的切片（H = `EMBEDDING__MAX_CHARS`，默认 1200 字）。

    - 不超过 H 的原样通过：短文本零开销、点 id 与改前逐字节相同；
    - 优先在换行处断，单行本身超上限才硬切（中文长段落没有换行，也拿得到上限保证）；
    - **不做 overlap**：本件是安全网不是分块策略，overlap 归 C20 之后的调优。

    `max_chars` 只给门禁/探针传参用（判据 ④ 要「把 H 调大即读数退化」的反向验证），生产留空读配置。
    """
    h = max_chars or settings.embedding.max_chars
    out: list[str] = []
    for t in texts:
        out += [t] if len(t) <= h else _cut(t, h)
    return out


def _cut(text: str, h: int) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    size = 0                                  # 当前块已占的字符数（含将要补的那个换行）
    for line in text.split("\n"):
        while len(line) > h:                  # 单行本身超上限：先硬切，余下的走缓冲
            if buf:
                chunks.append("\n".join(buf))
                buf, size = [], 0
            chunks.append(line[:h])
            line = line[h:]
        if buf and size + len(line) + 1 > h:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if c]
