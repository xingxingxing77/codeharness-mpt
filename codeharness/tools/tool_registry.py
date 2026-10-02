"""工具注册表（R8 拆开判之后剩下的那半件）。

源 `tools/tool_registry.py` 194 行同时干两件正交的事：
- **注册表数据结构 + 按名字/tag 选工具**：LangChain 没有这层，N2「声明式配置造角色」要用，本节自己写。
- **函数签名反射生成 YAML schema**：LangChain `@tool` / `args_schema` 白拿，判 `重` 不复制。
  因此源 `tool_data_type.py` 的 `Tool.schemas`/`Tool.code` 也没有读者（`role_zero.py:84` 只取
  `t.name`/`t.description`），一并 `弃`。

选择语义照源 `validate_tool_names`：一个 key 可以是工具名或 tag，取并集；未知 key 告警跳过而不是抛异常。
"""
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class ToolRegistry(BaseModel):
    """tools: name -> BaseTool；by_tag: tag -> {name: BaseTool}（两层 kv，照源结构）"""

    tools: dict[str, BaseTool] = {}
    by_tag: dict[str, dict[str, BaseTool]] = Field(default_factory=dict)

    def register(self, *items: BaseTool, tags: list[str] | None = None) -> None:
        tags = tags or []
        for t in items:
            self.tools[t.name] = t
            for tag in tags:
                self.by_tag.setdefault(tag, {})[t.name] = t

    def select(self, *keys: str) -> list[BaseTool]:
        """按名字或 tag 取并集，保持注册顺序；未知 key 告警跳过（源 validate_tool_names 语义）。"""
        picked: dict[str, BaseTool] = {}
        for key in keys:
            if key in self.tools:
                picked[key] = self.tools[key]
            elif key in self.by_tag:
                picked.update(self.by_tag[key])
            else:
                _warn(f"未知工具名或 tag: {key}，已跳过")
        return list(picked.values())

    def all(self) -> list[BaseTool]:
        return list(self.tools.values())

    def tags(self) -> list[str]:
        return list(self.by_tag)


def _warn(msg: str) -> None:
    from codeharness.logs import logger

    logger.warning(msg)


TOOL_REGISTRY = ToolRegistry()


def register_tool(tags: list[str] = None, **kwargs):
    """装饰器：签名照源 `register_tool(tags=..., schema_path=..., **kwargs)`。

    必须写在 `@tool` **之上**（后应用），这样收到的是 `@tool` 产出的 BaseTool。
    源版还要 inspect.getfile/getsource 反射出 YAML schema——那件由 LangChain 承担，所以这里只做登记。

    C121：收到非 BaseTool **当场抛 TypeError**——原来的静默 `_warn` 让 `libs/editor.py:80`
    那颗死装饰器（`include_functions` 整包被吞、装饰的是类不是工具）活了三个月没人察觉
    （对照3 §结论-3）。这类残留要装订时炸，不许带病入库。
    """

    def deco(obj):
        if not isinstance(obj, BaseTool):
            raise TypeError(f"{getattr(obj, '__name__', obj)!r} 不是 LangChain tool"
                            f"（漏写 @tool，或 @register_tool 写在了 @tool 之下？）")
        TOOL_REGISTRY.register(obj, tags=tags)
        return obj

    return deco
