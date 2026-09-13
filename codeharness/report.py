"""报道协议：BlockType 九值词汇表逐字对齐源 report.py:31-46；事件字段对齐 sessions.ts 装配逻辑。
内核任何位置 `await BlockReporter(...).report(...)` → 经 REPORT_SINK → server 总线 → 前端块。"""
import uuid as _uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar as _CV
from codeharness.runtime import REPORT_SINK, CURRENT_PROJECT

_ROLE: _CV[str] = _CV("report_role", default="")     # as_node 里 set_role(name) 注入


def set_role(name: str):
    _ROLE.set(name)


class BlockType:
    TERMINAL = "Terminal"
    TASK = "Task"
    BROWSER = "Browser"
    BROWSER_RT = "Browser-RT"
    EDITOR = "Editor"
    GALLERY = "Gallery"
    NOTEBOOK = "Notebook"
    DOCS = "Docs"
    THOUGHT = "Thought"


def _emit(block: str, uid: str, name: str, value, role: str = ""):
    sink = REPORT_SINK.get()
    if not sink:
        return
    sink({"block": block, "uuid": uid, "name": name, "value": value,
          "role": role or _ROLE.get()})


class BlockReporter:
    """一个块 = 一个 uuid；meta/document/path 一次性，cmd/output/content 可多次（前端流式追加）。"""

    def __init__(self, block: str, role: str = ""):
        self.block, self.role = block, role
        self.uuid = _uuid.uuid4().hex

    async def report(self, value, name: str):
        _emit(self.block, self.uuid, name, value, self.role)

    async def meta(self, value):    await self.report(value, "meta")
    async def content(self, text):  await self.report(str(text), "content")
    async def document(self, doc):  await self.report(doc, "document")   # {filename, content}
    async def path(self, p):        await self.report(str(p), "path")
    async def cmd(self, c):         await self.report(c, "cmd")
    async def output(self, line):   await self.report(line, "output")
    async def object(self, obj):    await self.report(obj, "object")
    async def close(self):          await self.report(None, "end_marker")

    @staticmethod
    def path_value(p) -> str:
        import os
        return os.path.abspath(str(p))


@asynccontextmanager
async def thought_block(role: str = "", meta: dict | None = None):
    """Thought 块：think 节点用。meta 默认 {"type": "react"}（前端 ThoughtBlock 的剧本标记）"""
    rep = BlockReporter(BlockType.THOUGHT, role)
    await rep.meta(meta or {"type": "react"})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def terminal_block(role: str = ""):
    """Terminal 块：shell/RunCode用——先 cmd 后 output 若干条"""
    rep = BlockReporter(BlockType.TERMINAL, role)
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def editor_block(filename: str, role: str = ""):
    """Editor 块：写代码文件用。meta 带 filename，前端标题显示文件名"""
    rep = BlockReporter(BlockType.EDITOR, role)
    await rep.meta({"type": "code", "filename": filename})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def docs_block(doc_type: str, role: str = ""):
    """Docs 块：PRD/设计文档用。meta.type 决定前端标题（DocsBlock.vue TYPE_NAMES）"""
    rep = BlockReporter(BlockType.DOCS, role)
    await rep.meta({"type": doc_type})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def task_block(role: str = ""):
    """Task 块：计划更新用（object 事件携带 plan dict）"""
    rep = BlockReporter(BlockType.TASK, role)
    try:
        yield rep
    finally:
        await rep.close()
