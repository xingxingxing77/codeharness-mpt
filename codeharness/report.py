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
    # 第十值：一次工具调用一个块。为什么要有它——`read_file`/`search_dir`/`search_file`/`find_file`
    # 与编辑器读那一类工具**一个块都不发**（只有 write_file 与 shell 有），所以对话流里
    # "每一步一行"无从谈起：缺的是发射，不是渲染。发射点只有一个接缝（`role_zero._act` 的
    # `self.tools[name].ainvoke`），所以一处改动覆盖全部工具，不去动 12 个工具函数。
    TOOL_CALL = "ToolCall"


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


def emit_event(kind: str, name: str = "", value=None, role: str = ""):
    """内核 → 事件流的**非块**出口（P2）：`sink` 收到带 `kind` 键的 event 时，server 侧
    `_make_sink` 直接按事件转发（不进块逻辑——不登记落点、不摘槽）。块走 `_emit`（带 block/uuid）、
    事件走这里——两条通道分开，块渲染器与落点表对事件零感知。前端对未知 kind 静默忽略（游标照推）。"""
    sink = REPORT_SINK.get()
    if not sink:
        return
    sink({"kind": kind, "name": name, "value": value, "role": role or (_role_name() or "")})


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
            from codeharness.logs import get_llm_stream_queue, logger
            q = get_llm_stream_queue()
            # C138（10-02 复审批）：补完腿全段带界——改前 `await q.put` 与 `await self._llm_task`
            # 都没有超时，消费任务一旦卡死 + 队列满档，这里**永久挂住**（「永不空的队列」＝挂死
            # 那一族，休眠桥也不该留着这族隐患）。三步各有界：alive 才投递（任务已死就只清
            # _spill 收内存）、put 5s 等不到就丢尾、收尾 10s 等不到就取消消费者——宁丢尾不挂死。
            alive = self._llm_task is not None and not self._llm_task.done()
            if q is not None and alive and getattr(q, "_spill", None):
                # C112 整段降级的补完腿：满档后攒下的逐片合成**一条** content 排在 None 之前入队，
                # 消费者照常逐条 async_report ⇒ 多出的这一条就是整段尾巴，正文一字不少（await 会等
                # 消费者腾出位置，降级只发生在消费者慢的那一段，正常场恒空、行为逐字不变）。
                try:
                    await asyncio.wait_for(q.put("".join(q._spill)), 5)
                except asyncio.TimeoutError:
                    logger.warning("[llm-stream] 补完腿 5s 没等到队列空位（消费者卡死？），丢尾——宁丢尾不挂死")
                q._spill = []
                try:
                    await asyncio.wait_for(q.put(None), 5)
                except asyncio.TimeoutError:
                    logger.warning("[llm-stream] 收尾哨兵 5s 没入队，丢——宁丢尾不挂死")
            elif q is not None:
                q._spill = []                     # 消费者已死/本就没有：_spill 是纯内存垃圾，收掉
            if self._llm_task:
                try:
                    await asyncio.wait_for(self._llm_task, 10)
                except asyncio.TimeoutError:
                    logger.warning("[llm-stream] 流消费者 10s 没收尾，已取消——宁丢尾不挂死")
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


def _meta_with_prose(meta: dict | None, prose) -> dict | None:
    """把 schema 声明的「正文字段」名单塞进这块自己的 meta（不另发控制事件、不加新分派键）。

    `prose_fields` 是给 `server/runner.py` 的打字机看的：逐片只放行名单里的字段，其余（枚举值、
    项目名、两份 mermaid 源码、`command_name`）留在定稿里一次性出现。名单**缺失**＝不门控，
    照旧只按长度挑——没声明的 schema 一个字节的行为都不变。前端只读 `meta.type`（DocsBlock
    的标题、ThoughtBlock 的剧本标记），多这一个键不影响任何标题/图标。
    """
    keys = getattr(prose, "prose_fields", None) if prose is not None else None
    return meta if keys is None else {**(meta or {}), "prose_fields": list(keys)}


@asynccontextmanager
async def thought_block(role: str = "", meta: dict | None = None, prose=None):
    """Thought 块：think 节点用。meta 默认 {"type": "react"}（前端 ThoughtBlock 的剧本标记）"""
    rep = BlockReporter(BlockType.THOUGHT.value, role)
    await rep.meta(_meta_with_prose(meta or {"type": "react"}, prose))
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
async def docs_block(doc_type: str, role: str = "", prose=None):
    """Docs 块：PRD/设计文档用。meta.type 决定前端标题（DocsBlock.vue TYPE_NAMES）"""
    rep = BlockReporter(BlockType.DOCS.value, role)
    await rep.meta(_meta_with_prose({"type": doc_type}, prose))
    try:
        yield rep
    finally:
        await rep.close()


def _brief_args(args: dict | None) -> dict:
    """参数摘要：每个值取一行 120 字。整份文件内容不许进事件流（那是产物，不是线索），
    但**键要留着**——前端靠它派生动词与摘要（照参照系 `SUMMARY_KEYS` 的取法：path/query/command…）。"""
    out = {}
    for k, v in (args or {}).items():
        s = str(v).split("\n")[0]
        out[k] = s if len(s) <= 120 else s[:117] + "…"
    return out


def recall_notice(*legs) -> list[str]:
    """R3：把「哪条读腿已经短路」写成给用户看的一行事实。**纯函数**（不自己发射，交给调用方手上
    那个 Thought 块），这样经典线哪天要接就是一行，不必再造第二个措辞。
    今天只有一个用户：动态线 `RoleZero._think`。经典线那两条读腿跑在 `Agent._act` 里、那里没有
    Thought 块作用域 ⇒ 本批不为此新造发射器，经典线的召回状态只在用量页可见（已留账）。

    为什么必须有这行：召回挂了此前只有日志 warning 与用量页计数，对话流里看不出来，而用户在那儿看到的
    表现是「这条回答没引文档」——与「知识库里没相关内容」完全同形（C96 那一族：把故障演成空态）。
    措辞只写数得出的事实：腿名、本场此腿不再尝试、后端留下的原始异常类名。**不猜原因**（C16：
    不许写成「存储不可用」那种替用户下结论的话）。
    """
    out = []
    for leg in legs:
        if leg is not None and not getattr(leg, "up", True):
            out.append(f"[召回不可用] {leg.doc_type} 腿本场已跳过："
                       f"{getattr(leg, 'last_error', '') or '原因见日志'}")
    return out


async def tool_call_open(name: str, args: dict | None, role: str = "") -> BlockReporter:
    """执行**前**把工具卡立起来：meta 先到 ⇒ 工具在跑的那段时间里界面上就有这一行（状态 running）。

    改前这一行只在 `ainvoke` 返回之后才发（`tool_call_report` 把 meta/content/close 一次做完），
    于是 `_act` 里那些耗时工具（写代码动辄几十秒、长 `shell` 命令）执行期间屏上**什么都没有**——
    C177 把 `_act` 那族产代码的块外调用挡下打字机之后，这段空窗更显眼。
    `ok` 此刻还不知道（结果没回来），留到 `tool_call_report(..., rep=card)` 那一次 meta 覆盖。
    """
    rep = BlockReporter(BlockType.TOOL_CALL.value, role)
    await rep.meta({"type": "tool_call", "tool": name, "args": _brief_args(args)})
    return rep


async def tool_call_report(name: str, args: dict | None, out, ok: bool = True, role: str = "",
                           rep: Optional[BlockReporter] = None):
    """一次工具调用 → 一个 ToolCall 块（meta 带工具名与参数摘要，正文带结果首行）。

    `rep` 给了就复用它：`_act` 用 `tool_call_open` 在执行前已把这一行立起来，这里是**同一颗 uuid**
    的第二次 meta（把 `ok` 补上）+ 正文 + 收口；没给就现开一张（被拒分支那种一次性调用点）。

    与参照系的差别要写清：它那一行 `Read · app\api\admin.py` 的"标题"也不是模型给的，
    而是「变体名 + 从 args 派生的摘要」（`ui-tool/src/client/tool/models/tool-call-model.ts`
    的 `SUMMARY_KEYS`）——所以我们只发**事实**（工具名 + 参数 + 结果摘要），
    动词与摘要由前端派生，不新造一个"意图"字段去求模型填（那要动 prompt，属 C6 那类风险）。
    """
    rep = rep if rep is not None else BlockReporter(BlockType.TOOL_CALL.value, role)
    await rep.meta({"type": "tool_call", "tool": name, "args": _brief_args(args), "ok": ok})
    await rep.content(f"[已拒绝] 未获批准，不执行" if not ok else str(out).split("\n")[0][:400])
    await rep.close()


@asynccontextmanager
async def task_block(role: str = "", prose=None):
    """Task 块：计划更新用（object 事件携带 plan dict）。

    开块就发一条 `meta`：`LIVE_BLOCKS` 里本来就有 `Task`，而落点表只在报道槽转发处登记——改前这一条通道上
    一个事件都没有（`task_block` 只建 `BlockReporter` 就 yield，第一下事件是调用结束后的 `content`），
    所以那一笔调用的逐片只能落兜底行 `stream-{node}`，既不与清单同块、名单也挂不上。
    这是照源码推出的形状，活体没走到过 WriteTasks（09-29 两场经典线都在 Design 就收了），
    改后的读数在 `tests/manual_stream_landing.py` ⑦ 与 `tests/s8_runner_meter.py` t12⑨。
    """
    rep = BlockReporter(BlockType.TASK.value, role)
    await rep.meta(_meta_with_prose({"type": "tasks"}, prose) or {})
    try:
        yield rep
    finally:
        await rep.close()


def _md_lines(val, depth: int = 0) -> list:
    """把嵌套的 list/dict 摊成 markdown 列表行（值只到标量为止）。"""
    pad = "  " * depth
    if isinstance(val, dict):
        out = []
        for k, v in val.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}- {k}:")
                out += _md_lines(v, depth + 1)
            elif v:
                out.append(f"{pad}- {k}: {v}")
        return out
    if isinstance(val, list):
        out = []
        for item in val:
            if isinstance(item, list):
                out.append(f"{pad}- " + " | ".join(str(x) for x in item))   # 一行一条（requirement_pool 那种表）
            elif isinstance(item, dict):
                # 一条记录 = 一个子弹头，其余字段缩进在它下面（平铺会让「哪几行属于同一条」丢掉）
                lines = _md_lines(item, 0)
                if lines:
                    head, *rest = lines
                    out.append(f"{pad}- {head[2:]}")
                    out += [f"{pad}  {l}" for l in rest]
            else:
                out.append(f"{pad}- {item}")
        return out
    return [f"{pad}{val}"]


def block_markdown(model) -> str:
    """schema 实例 → 「字段名 + 值」的 markdown，Docs/Task 块的正文口径。

    为什么不发 `model_dump_json()`：那块面是按 markdown 渲染的正文（`ChatNode.vue` 的 Docs 走
    `MarkdownText`，Task 行也只是标题 + 文本，前端没有任何一处 `JSON.parse` 块正文），塞进去的
    `{"language": "en", ...}` 就是一大坨读不懂的字符——09-28 实测一跑里 PRD 块 4965 字、Design 块
    5741 字全是 JSON，用户报的「输出会带有一大块文本出现」有另一半在这里。
    落盘产物不受影响：机器要解析的那几份（`prd.json`/`design.json`/`tasks`）照旧是 JSON。
    """
    parts = []
    for key, val in model.model_dump().items():
        title = "## " + key.replace("_", " ")
        if isinstance(val, str):
            if val.strip():
                parts.append(f"{title}\n{val.strip()}")
        elif val:
            parts.append(title + "\n" + "\n".join(_md_lines(val)))
    return "\n\n".join(parts) + "\n"
