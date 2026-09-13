"""libs 移植垫片：源 metagpt 依赖的最小等价实现（editor/git 复制件用）。
MVP 中 editor 命令未接入 REGISTRY（write_file 覆盖），垫片只为 import 不炸、语义不歪。"""
import asyncio
from pathlib import Path


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


class Linter:
    def __init__(self, root=None):
        self.root = root

    async def lint(self, filename=None, return_dict=False):
        """返回空 = 无 lint 错误（源语义：错误列表/字典）；接 ruff 属后续增强"""
        return {} if return_dict else []


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
