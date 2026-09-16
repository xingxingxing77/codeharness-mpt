"""libs 移植垫片：源 metagpt 依赖的最小等价实现（editor 复制件用）。
`Linter` 已换成 `libs/linter.py` 的真实现（原来的假 `Linter` 让 editor 的"改完自动 lint"变成空话）。
`register_tool` 保持 no-op：editor.py 是 `复` 件，类级的源装饰器不参与本仓注册面——
真正的工具面在 `editor_tools.py`（逐方法包 @tool + 真 register_tool，接线台账 #7 已通电）。"""
import asyncio
from pathlib import Path

from codeharness.tools.libs.linter import Linter  # editor.py:206 的真消费者


def register_tool(**kw):
    """源 tool_registry.register_tool 的 no-op 版：注册表职责已由 LangChain @tool 接管"""
    def deco(obj):
        return obj
    return deco


DEFAULT_MIN_TOKEN_COUNT = 80
DEFAULT_WORKSPACE_ROOT = Path("workspace")


async def awrite(filename, data, encoding="utf-8"):
    p = Path(filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(p.write_text, str(data), encoding=encoding)


class File:
    @staticmethod
    async def read_text_file(path) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    @staticmethod
    async def write_text_file(path, content) -> bool:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_text, content, encoding="utf-8")
        return True


class EditorReporter:
    """报道块的兼容壳：有 REPORT_SINK 时走内核报道，否则静默"""
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def async_report(self, value, name: str = "meta", **kw):
        from codeharness.report import _emit, BlockType, _uuid
        _emit(BlockType.EDITOR, _uuid.uuid4().hex, name, value)

    def report(self, value, name: str = "meta", **kw):
        pass
