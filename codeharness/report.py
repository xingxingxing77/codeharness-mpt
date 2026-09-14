"""报道协议。判定 `改`（R6）：词汇表与类名逐字对齐，通道换实现。

两件事必须成立，否则前端或迁移过来的 Action 会静默/当场坏：
1. **九值 BlockType 词汇表**逐字一致 —— 错一个词，前端富块全部降级成灰色 GenericBlock，
   而且**自测看不出来**，只在浏览器里暴露。
2. **12 个 Reporter 类名 + `report`/`async_report` 签名**齐备 —— 调用方是按类名用的
   （如 `TaskReporter().report({...})`），缺一个就 NameError。

通道：源用 HTTP `callback_url` POST，这里改写内核报道槽（`runtime.REPORT_SINK`），
由 server 侧桥到事件总线 → SSE。少一次网络往返，且自测不必起 HTTP 服务。
类名、字段名、方法名与调用约定保持一致。
"""
import asyncio
import os
import typing
import uuid as _uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PrivateAttr

from codeharness.runtime import REPORT_SINK

if typing.TYPE_CHECKING:
    from codeharness.roles.agent import Agent

CURRENT_ROLE: ContextVar["Agent"] = ContextVar("role")

END_MARKER_NAME = "end_marker"
END_MARKER_VALUE = "\x18\x19\x1B\x18\n"


class BlockType(str, Enum):
    """Enumeration for different types of blocks.（九值）"""

    TERMINAL = "Terminal"
    TASK = "Task"
    BROWSER = "Browser"
    BROWSER_RT = "Browser-RT"
    EDITOR = "Editor"
    GALLERY = "Gallery"
    NOTEBOOK = "Notebook"
    DOCS = "Docs"
    THOUGHT = "Thought"


def _role_name() -> Optional[str]:
    """当前报道归属的角色名：优先 ContextVar，其次环境变量。"""
    role = CURRENT_ROLE.get(None)
    if role is not None:
        return getattr(role, "name", None) or str(role)
    return os.environ.get("CODEHARNESS_ROLE") or None


def set_role(name):
    """内核在角色节点入口注入，使块事件的 role 字段正确（前端块头显示角色名）。"""
    CURRENT_ROLE.set(name)
    return CURRENT_ROLE


def _emit(block, uid: str, name: str, value, role: str = "", extra: Optional[dict] = None):
    """唯一出口：写入内核报道槽。没装桥（自测场景）就静默丢弃，不影响业务流。"""
    sink = REPORT_SINK.get()
    if not sink:
        return
    event = {"block": block, "uuid": uid, "name": name, "value": value, "role": role or (_role_name() or "")}
    if extra:
        event["extra"] = extra
    sink(event)


class ResourceReporter(BaseModel):
    """Base class for resource reporting.

    `callback_url` 保留为兼容字段但不参与发送 —— 发送通道是报道槽 + SSE。
    """

    block: BlockType = Field(description="The type of block that is reporting the resource")
    uuid: UUID = Field(default_factory=uuid4, description="The unique identifier for the resource")
    enable_llm_stream: bool = Field(False, description="Indicates whether to connect to an LLM stream for reporting")
    callback_url: str = Field("", description="保留字段：通道为报道槽，不再走 HTTP 回调")
    _llm_task: Optional[asyncio.Task] = PrivateAttr(None)

    def report(self, value: Any, name: str, extra: Optional[dict] = None):
        """Synchronously report resource observation data."""
        return self._report(value, name, extra)

    async def async_report(self, value: Any, name: str, extra: Optional[dict] = None):
        """Asynchronously report resource observation data."""
        return await self._async_report(value, name, extra)

    @classmethod
    def set_report_fn(cls, fn: Callable):
        """Set the synchronous report function（与源同为类属性替换）。"""
        cls._report = fn

    @classmethod
    def set_async_report_fn(cls, fn: Callable):
        """Set the asynchronous report function."""
        cls._async_report = fn

    def _report(self, value: Any, name: str, extra: Optional[dict] = None):
        data = self._format_data(value, name, extra)
        return _emit(data["block"], data["uuid"], data["name"], data["value"],
                     data["role"], extra)

    async def _async_report(self, value: Any, name: str, extra: Optional[dict] = None):
        return self._report(value, name, extra)

    def _role(self) -> str:
        return _role_name() or ""

    @staticmethod
    def _plain(value):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Path):
            return str(value)
        return value

    def _format_data(self, value, name, extra=None) -> dict:
        """与源同构的载荷：name == "path" 时转绝对路径。"""
        value = self._plain(value)
        if name == "path":
            value = os.path.abspath(str(value))
        data = {"block": self.block.value, "uuid": str(self.uuid), "value": value,
                "name": name, "role": self._role()}
        if extra:
            data["extra"] = extra
        return data

    def __enter__(self):
        """Enter the synchronous streaming callback context."""
        return self

    def __exit__(self, *args, **kwargs):
        """Exit the synchronous streaming callback context."""
        self.report(None, END_MARKER_NAME)

    async def __aenter__(self):
        """Enter the asynchronous streaming callback context."""
        if self.enable_llm_stream:
            from codeharness.logs import create_llm_stream_queue
            self._llm_task = asyncio.create_task(self._llm_stream_report(create_llm_stream_queue()))
        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        """Exit the asynchronous streaming callback context."""
        if self.enable_llm_stream and exc_type != asyncio.CancelledError:
            from codeharness.logs import get_llm_stream_queue
            await get_llm_stream_queue().put(None)
            if self._llm_task:
                await self._llm_task
            self._llm_task = None
        await self.async_report(None, END_MARKER_NAME)

    async def _llm_stream_report(self, queue: asyncio.Queue):
        while True:
            data = await queue.get()
            if data is None:
                return
            await self.async_report(data, "content")

    async def wait_llm_stream_report(self):
        """Wait for the LLM stream report to complete."""
        from codeharness.logs import get_llm_stream_queue
        queue = get_llm_stream_queue()
        while self._llm_task:
            if queue.empty():
                break
            await asyncio.sleep(0.01)


class TerminalReporter(ResourceReporter):
    """Terminal output callback for streaming reporting of command and output.

    终端有状态、一个角色可开多个终端，所以每个终端要独立实例。
    """

    block: BlockType = BlockType.TERMINAL

    def report(self, value: str, name: str = "cmd"):
        """Report terminal command or output synchronously."""
        return super().report(value, name)

    async def async_report(self, value: str, name: str = "cmd"):
        """Report terminal command or output asynchronously."""
        return await super().async_report(value, name)


class BrowserReporter(ResourceReporter):
    """Browser output callback for streaming reporting of requested URL and page content."""

    block: BlockType = BlockType.BROWSER

    def report(self, value, name: str = "url"):
        """Report browser URL or page content synchronously."""
        if name == "page":
            value = {"page_url": value.url, "title": value.title(), "screenshot": str(value.screenshot())}
        return super().report(value, name)

    async def async_report(self, value, name: str = "url"):
        """Report browser URL or page content asynchronously."""
        if name == "page":
            value = {"page_url": value.url, "title": await value.title(), "screenshot": str(await value.screenshot())}
        return await super().async_report(value, name)


class ServerReporter(ResourceReporter):
    """Callback for server deployment reporting."""

    block: BlockType = BlockType.BROWSER_RT

    def report(self, value: str, name: str = "local_url"):
        """Report server deployment synchronously."""
        return super().report(value, name)

    async def async_report(self, value: str, name: str = "local_url"):
        """Report server deployment asynchronously."""
        return await super().async_report(value, name)


class ObjectReporter(ResourceReporter):
    """Callback for reporting complete object resources."""

    block: BlockType = BlockType.TASK

    def report(self, value: dict, name: str = "object"):
        """Report object resource synchronously."""
        return super().report(value, name)

    async def async_report(self, value: dict, name: str = "object"):
        """Report object resource asynchronously."""
        return await super().async_report(value, name)


class TaskReporter(ObjectReporter):
    """Reporter for object resources to Task Block."""

    block: BlockType = BlockType.TASK


class ThoughtReporter(ObjectReporter):
    """Reporter for object resources to Thought Block."""

    block: BlockType = BlockType.THOUGHT


class FileReporter(ResourceReporter):
    """File resource callback for reporting complete file paths.

    整体一次性输出用非流式回调；可分段展示用流式回调。
    """

    block: BlockType = BlockType.DOCS

    def report(self, value, name: str = "path", extra: Optional[dict] = None):
        """Report file resource synchronously."""
        return super().report(value, name, extra)

    async def async_report(self, value, name: str = "path", extra: Optional[dict] = None):
        """Report file resource asynchronously."""
        return await super().async_report(value, name, extra)


class NotebookReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.NOTEBOOK)."""

    block: BlockType = BlockType.NOTEBOOK


class DocsReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.DOCS)."""

    block: BlockType = BlockType.DOCS


class EditorReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.EDITOR)."""

    block: BlockType = BlockType.EDITOR


class GalleryReporter(FileReporter):
    """Image resource callback for reporting complete file paths.

    图片要完整才展示，所以每次回调是完整路径；但 Gallery 要显示类型与 prompt，
    所以有 meta 时按流式上报。
    """

    block: BlockType = BlockType.GALLERY

    def report(self, value, name: str = "path"):
        """Report image resource synchronously."""
        return super().report(value, name)

    async def async_report(self, value, name: str = "path"):
        """Report image resource asynchronously."""
        return await super().async_report(value, name)


# ============ 平台自有：上下文管理器式块（server/前端已按此装配，保留不动） ============
class BlockReporter:
    """一个块 = 一个 uuid；meta/document/path 一次性，cmd/output/content 可多次（前端流式追加）。"""

    def __init__(self, block, role: str = ""):
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
    async def close(self):          await self.report(None, END_MARKER_NAME)

    @staticmethod
    def path_value(p) -> str:
        return os.path.abspath(str(p))


@asynccontextmanager
async def thought_block(role: str = "", meta: dict | None = None):
    """Thought 块：think 节点用。meta 默认 {"type": "react"}（前端 ThoughtBlock 的剧本标记）"""
    rep = BlockReporter(BlockType.THOUGHT.value, role)
    await rep.meta(meta or {"type": "react"})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def terminal_block(role: str = ""):
    """Terminal 块：shell/RunCode 用——先 cmd 后 output 若干条"""
    rep = BlockReporter(BlockType.TERMINAL.value, role)
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def editor_block(filename: str, role: str = ""):
    """Editor 块：写代码文件用。meta 带 filename，前端标题显示文件名"""
    rep = BlockReporter(BlockType.EDITOR.value, role)
    await rep.meta({"type": "code", "filename": filename})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def docs_block(doc_type: str, role: str = ""):
    """Docs 块：PRD/设计文档用。meta.type 决定前端标题（DocsBlock.vue TYPE_NAMES）"""
    rep = BlockReporter(BlockType.DOCS.value, role)
    await rep.meta({"type": doc_type})
    try:
        yield rep
    finally:
        await rep.close()


@asynccontextmanager
async def task_block(role: str = ""):
    """Task 块：计划更新用（object 事件携带 plan dict）"""
    rep = BlockReporter(BlockType.TASK.value, role)
    try:
        yield rep
    finally:
        await rep.close()
