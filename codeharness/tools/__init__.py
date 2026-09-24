"""工具层：@tool 造 LangChain 工具，@register_tool 登记进 TOOL_REGISTRY（R8 剩下的那半件）。
工具输出统一打 log_tool_output（第 1 步 logs.py 的槽）+ 报道块（report.py）——前端面板数据源。
文件与命令工具的边界 = runtime.session_root()，按会话隔离（S4 判 `新`）。"""
from langchain_core.tools import tool
from codeharness.logs import ToolLogItem, log_tool_output
from codeharness.tools._boundary import safe_session_path as _safe   # 边界判据统一放 _boundary（台账 #7）
from codeharness.tools.tool_registry import TOOL_REGISTRY, register_tool


@register_tool(tags=["file"])
@tool
async def write_file(path: str, content: str) -> str:
    """整份写入工作区文件：路径相对会话根目录，越界即拒；父目录自动创建，已存在则整篇覆盖。
    关键词：写入、保存、存成、另存为、覆盖、导出、落盘、write、save。
    示例：write_file(path="note.txt", content="第一行")"""
    from codeharness.report import editor_block
    t = _safe(path)
    if not t:
        return "拒绝：路径越界"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(content, encoding="utf-8")
    log_tool_output(ToolLogItem(name="write_file", value=path))
    async with editor_block(filename=path) as rep:          # 前端 Editor 块（代码卡）
        await rep.document({"filename": path, "content": content})
    return f"已写入 {path}（{len(content)} 字符）"


@register_tool(tags=["file"])
@tool
def read_file(path: str) -> str:
    """读取工作区文件的全部内容（相对路径，越界即拒；单次最多回 2 万字符）。
    关键词：读、看、查看、打开看看、里面写了什么、念一下、全文、read、show、内容。
    示例：read_file(path="src/main.py")"""
    t = _safe(path)
    if not t or not t.exists():
        return f"文件不存在: {path}"
    return t.read_text(encoding="utf-8")[:20000]


@register_tool(tags=["terminal"])
@tool
async def execute_shell_async(command: str, timeout: int = 60) -> str:
    """在本会话工作目录跑一条 shell 命令并等它结束（异步版，图节点里用；默认 60 秒超时，回 stdout+stderr）。
    关键词：执行、跑、运行、跑一下、起一下、命令、脚本、构建、编译、装依赖、测试、lint、shell、bash。
    示例：execute_shell_async(command="pytest -q", timeout=120)"""
    from codeharness.report import terminal_block
    from codeharness.tools.sandbox import run_proc
    async with terminal_block() as rep:                     # 前端 Terminal 块（cmd+output 流式）
        await rep.cmd(command)
        r = await run_proc(command, shell=True, timeout=timeout)
        text = ((r.stdout + r.stderr).strip() or "(无输出)")[:10000]
        await rep.output(text)
        return text


@register_tool(tags=["web"])
@tool
async def search_internet(query: str) -> str:
    """联网搜索，返回「标题 + 链接 + 摘要」列表（本会话唯一的外网出口；引擎不可用时回降级文案而不抛异常）。
    关键词：搜索、查一下、网上找、官方文档、资料、报错怎么解、最新版本、search、google、docs。
    示例：search_internet(query="langchain astream_events 用法")"""
    from codeharness.tools.search_engine import search
    try:
        rows = await search(query)
    except Exception as e:
        return f"[搜索暂不可用: {e}]"
    text = "\n\n".join(f"{i}. {r['title']}\n   {r['link']}\n   {r['snippet']}"
                       for i, r in enumerate(rows, 1))
    return (text or "[搜索无结果]")[:8000]


@register_tool(tags=["retrieval"])
@tool
async def search_knowledge_base(query: str) -> str:
    """在本会话的知识库里按语义再查一次，返回「出处 + 原文片段」列表。首轮预取没答上、或要换个说法核实那份文档时用；只查本会话自己上传的文档，不联网（联网用 search_internet）。
    关键词：查知识库、知识库里、文档里、那份文档、资料里怎么说、手册里、再查一次、再找一下、出处、依据、原文、有没有写过、kb、knowledge。
    示例：search_knowledge_base(query="差旅报销的额度上限是多少")"""
    from codeharness.configs.settings import settings
    if not settings.enable_rag:
        return "[知识库检索未启用：本部署关掉了 RAG（settings.enable_rag=False）]"
    # 读者每次现建、不缓存成模块级单例：客户端会绑在首个事件循环上，而门禁一个进程里跑多个 asyncio.run。
    # 租户与项目都不传 —— 走 LongTermMemory 的 ContextVar 兜底（CURRENT_USER/CURRENT_PROJECT），
    # 与灌库侧、与每轮预取那条读者同源（C31 修的就是「两处各算一次同一个身份」）。
    # ponytail: 一次调用 = 一对新客户端（Qdrant + embeddings），连接靠 GC 收。上限是「模型高频调它」；
    # 真到那一步再给 QdrantStore 加进程级 client 复用，现在不为没出现的调用方造池子。
    from codeharness.logs import logger
    from codeharness.memory.longterm import LongTermMemory, format_kb_blocks
    from codeharness.provider.gateway import LLMGateway
    kb = LongTermMemory(embeddings=LLMGateway.embeddings(), doc_type="kb")
    try:
        blocks = format_kb_blocks(await kb.recall(query, k=3))
    except Exception as e:
        # 与 _kb_recall 同一档位：检索挂了不打断这场。类名照原样带出去，不写成「存储不可用」的谎（C16）
        logger.warning(f"知识库检索工具失败: {type(e).__name__}: {e}")
        return f"[知识库检索暂不可用: {type(e).__name__}: {e}]"
    return (blocks or "[知识库里没有与这句相关的切片]")[:8000]


from codeharness.tools.libs import terminal as _terminal      # noqa: F401  副作用：terminal_command 登记
from codeharness.tools.libs import editor_tools as _editor    # noqa: F401  副作用：Editor 11 命令登记（台账 #7）
from codeharness.tools.libs import git as _git                # noqa: F401  副作用：git 两件登记（台账 #8）

REGISTRY = TOOL_REGISTRY.all()      # 全量视图；按 profile 选子集用 TOOL_REGISTRY.select(name|tag)
# 只 import 真已移植的 libs——源项目 libs/__init__.py 全量 eager import，会把 editor.py(1,135) 拖进导入路径。
# 源 libs/browser.py(211) + web_browser_engine_playwright.py(146) 判「推迟」（docs/施工2 §S4，实测三条）：
# playwright 未装、它 import 的 utils/a11y_tree.py 本仓没有、且全仓零调用者。要交互浏览就和 per-session 容器同批做。
