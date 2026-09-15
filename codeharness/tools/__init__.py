"""工具层：@tool 造 LangChain 工具，@register_tool 登记进 TOOL_REGISTRY（R8 剩下的那半件）。
工具输出统一打 log_tool_output（第 1 步 logs.py 的槽）+ 报道块（report.py）——前端面板数据源。"""
import asyncio
from pathlib import Path
from langchain_core.tools import tool
from codeharness.configs.settings import settings
from codeharness.logs import ToolLogItem, log_tool_output
from codeharness.tools.tool_registry import TOOL_REGISTRY, register_tool


def _root() -> Path:
    return Path(settings.workspace_root).resolve()


def _safe(path: str) -> Path | None:
    root = _root()
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
    """在 workspace 目录执行 shell 命令（异步版，图节点里用）"""
    from codeharness.report import terminal_block
    async with terminal_block() as rep:                     # 前端 Terminal 块（cmd+output 流式）
        await rep.cmd(command)
        p = await asyncio.create_subprocess_shell(
            command, cwd=_root(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            await p.communicate()                 # 收尸：不 drain 会在 Windows 上留 unclosed transport
            await rep.output(f"[超时 {timeout}s] {command}")
            return f"[超时 {timeout}s] {command}"
        text = ((out.decode(errors="replace") + err.decode(errors="replace")).strip() or "(无输出)")[:10000]
        await rep.output(text)
        return text


@register_tool(tags=["web"])
@tool
def search_internet(query: str) -> str:
    """联网搜索。实现：langchain_community DuckDuckGo（无 key）；限流时返回降级文案"""
    try:
        from langchain_community.tools.ddg_search.tool import DuckDuckGoSearchRun
        return DuckDuckGoSearchRun().run(query)[:8000]
    except Exception as e:
        return f"[搜索暂不可用: {e}]"


REGISTRY = TOOL_REGISTRY.all()      # 全量视图；按 profile 选子集用 TOOL_REGISTRY.select(name|tag)
# git_run / scrape_web：tools/libs/git.py、web_scraping.py 复制后按 write_file 同样方式包 @tool（各 ~10 行）
