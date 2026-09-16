"""register_tool：往 S4 工具注册表加新工具（RoleZero 命令面与 REGISTRY select 即时可见）。

同步进 `codeharness.tools.REGISTRY` 那个**列表对象本身**（append，不换引用）——
team/registry 的角色工厂拿着同一个 list 引用构建工具集，换新列表老引用就看不到，
这是本件唯一需要小心的既有事实。
"""
from codeharness.tools import REGISTRY
from codeharness.tools.tool_registry import TOOL_REGISTRY


def register_tool(tool, tags: list[str] | None = None):
    """tool：langchain @tool 装饰过的 BaseTool（或裸 callable，自动包装）。"""
    from langchain_core.tools import BaseTool
    if callable(tool) and not isinstance(tool, BaseTool):
        from langchain_core.tools import tool as _as_tool
        tool = _as_tool(tool)
    if not isinstance(tool, BaseTool):
        raise TypeError(f"register_tool 需要 @tool 或 callable，收到 {type(tool).__name__}")
    names = {t.name for t in REGISTRY}
    if tool.name in names:
        raise ValueError(f"工具 {tool.name!r} 已存在，不允许覆盖内核件")
    TOOL_REGISTRY.register(tool, tags=tags or ["ext"])
    REGISTRY.append(tool)
    return tool
