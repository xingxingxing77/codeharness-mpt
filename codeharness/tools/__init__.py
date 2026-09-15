"""工具层：@tool 造 LangChain 工具，@register_tool 登记进 TOOL_REGISTRY（R8 剩下的那半件）。
工具输出统一打 log_tool_output（第 1 步 logs.py 的槽）+ 报道块（report.py）——前端面板数据源。
文件与命令工具的边界 = runtime.session_root()，按会话隔离（S4 判 `新`）。"""
from pathlib import Path
from langchain_core.tools import tool
from codeharness.logs import ToolLogItem, log_tool_output
from codeharness.runtime import session_root
from codeharness.tools.tool_registry import TOOL_REGISTRY, register_tool


def _safe(path: str) -> Path | None:
    root = session_root()          # per-session 边界：会话 A 的工具写不进会话 B 的目录
    target = (root / path).resolve()
    # 必须用 is_relative_to：str.startswith 会把兄弟目录 ws_probe 当成 ws 之内（前缀命中）而放行
    return target if target.is_relative_to(root) else None


@register_tool(tags=["file"])
@tool
async def write_file(path: str, content: str) -> str:
    """写入工作区文件（相对路径，禁止越界），返回确认信息"""
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
    """读取工作区文件内容"""
    t = _safe(path)
    if not t or not t.exists():
        return f"文件不存在: {path}"
    return t.read_text(encoding="utf-8")[:20000]


@register_tool(tags=["terminal"])
@tool
async def execute_shell_async(command: str, timeout: int = 60) -> str:
    """在本会话工作目录执行 shell 命令（异步版，图节点里用）"""
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
    """联网搜索，返回「标题 + 链接 + 摘要」列表；引擎不可用时返回降级文案而不是抛异常"""
    from codeharness.tools.search_engine import search
    try:
        rows = await search(query)
    except Exception as e:
        return f"[搜索暂不可用: {e}]"
    text = "\n\n".join(f"{i}. {r['title']}\n   {r['link']}\n   {r['snippet']}"
                       for i, r in enumerate(rows, 1))
    return (text or "[搜索无结果]")[:8000]


from codeharness.tools.libs import terminal as _terminal  # noqa: F401  副作用：terminal_command 登记进 TOOL_REGISTRY

REGISTRY = TOOL_REGISTRY.all()      # 全量视图；按 profile 选子集用 TOOL_REGISTRY.select(name|tag)
# 待接：git_run / Editor 命令面 按同样方式包 `@register_tool(tags=[...]) + @tool`（各 ~10 行）。
# 只 import 真已移植的 libs——源项目 libs/__init__.py 全量 eager import，会把 editor.py(1,135) 拖进导入路径。
# 源 libs/browser.py(211) + web_browser_engine_playwright.py(146) 判「推迟」（docs/施工2 §S4，实测三条）：
# playwright 未装、它 import 的 utils/a11y_tree.py 本仓没有、且全仓零调用者。要交互浏览就和 per-session 容器同批做。
