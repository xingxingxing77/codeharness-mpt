"""Editor 命令面进注册表（接线台账 #7）。源 RoleZero 默认装配 `Editor.*` 十四法
（roles/di/role_zero.py:147-167）里，本仓接 11 只编辑/查看/搜索命令；去向登记：
- `read`/`write` 不另挂：已有带会话边界的 read_file/write_file（Editor 版自身对绝对路径直解，
  再挂=给模型开第二条无边界写路）；
- `similarity_search` 已随 index_repo 判弃，方法体从 editor.py 删除（台账 #9）。
Editor 复制件的 `_try_fix_path` 只把相对路径并进 working_dir、绝对路径原样放行——那是源
直跑 CLI 的口径；工具面每命令先过 safe_session_path 再进 Editor，边界不白送。
按会话一个 Editor 实例：current_file/current_line 的视图态属会话私有（与 Terminal._TERMINALS 同构）。
"""
from langchain_core.tools import tool

from codeharness.runtime import CURRENT_PROJECT, session_root
from codeharness.tools._boundary import safe_session_path
from codeharness.tools.libs.editor import Editor
from codeharness.tools.tool_registry import register_tool

_EDITORS: dict[str, Editor] = {}


def current_editor() -> Editor:
    key = CURRENT_PROJECT.get()
    ed = _EDITORS.get(key)
    if ed is None:
        ed = _EDITORS[key] = Editor(working_dir=session_root())
    return ed


def close_editor(project: str | None = None) -> None:
    """散会销账（与 runner._forget 收壳同批；Editor 无常驻进程，纯清视图态）。"""
    _EDITORS.pop(project if project is not None else CURRENT_PROJECT.get(), None)


def _inside(path: str) -> str | None:
    """越界返回 None；在界内原样回传（Editor 自己按 working_dir 拼相对路径）。"""
    return path if safe_session_path(path) else None


@register_tool(tags=["edit"])
@tool
def open_file(path: str, line_number: int = 1) -> str:
    """打开会话内文件并显示窗口（默认前 100 行；goto_line/scroll_down/scroll_up 翻页）"""
    p = _inside(path)
    return "拒绝：路径越界" if p is None else current_editor().open_file(p, line_number=line_number or 1)


@register_tool(tags=["edit"])
@tool
def goto_line(line_number: int) -> str:
    """当前打开文件的窗口跳到指定行号"""
    return current_editor().goto_line(line_number)


@register_tool(tags=["edit"])
@tool
def scroll_down() -> str:
    """当前文件向下翻页"""
    return current_editor().scroll_down()


@register_tool(tags=["edit"])
@tool
def scroll_up() -> str:
    """当前文件向上翻页"""
    return current_editor().scroll_up()


@register_tool(tags=["edit"])
@tool
async def create_file(filename: str) -> str:
    """在会话内创建空文件（自动建父目录），随后用 append_file/edit_file_by_replace 填内容"""
    f = _inside(filename)
    return "拒绝：路径越界" if f is None else await current_editor().create_file(f)


@register_tool(tags=["edit"])
@tool
def edit_file_by_replace(file_name: str, first_replaced_line_number: int, first_replaced_line_content: str,
                         last_replaced_line_number: int, last_replaced_line_content: str,
                         new_content: str) -> str:
    """把 file_name 的第 first..last 行（含）替换为 new_content（空串=删除该段）。
    两端行内容指纹校验防错位；行号从 1 起，缩进需完整给出。"""
    f = _inside(file_name)
    if f is None:
        return "拒绝：路径越界"
    return current_editor().edit_file_by_replace(
        f, first_replaced_line_number, first_replaced_line_content,
        last_replaced_line_number, last_replaced_line_content, new_content)


@register_tool(tags=["edit"])
@tool
def insert_content_at_line(file_name: str, line_number: int, insert_content: str) -> str:
    """在 file_name 的第 line_number 行前插入内容"""
    f = _inside(file_name)
    if f is None:
        return "拒绝：路径越界"
    return current_editor().insert_content_at_line(f, line_number, insert_content)


@register_tool(tags=["edit"])
@tool
def append_file(file_name: str, content: str) -> str:
    """把 content 追加到 file_name 末尾"""
    f = _inside(file_name)
    return "拒绝：路径越界" if f is None else current_editor().append_file(f, content)


@register_tool(tags=["edit"])
@tool
def search_dir(search_term: str, dir_path: str = "./") -> str:
    """在目录里全文搜索，返回 文件:行号:内容 列表"""
    d = _inside(dir_path)
    return "拒绝：路径越界" if d is None else current_editor().search_dir(search_term, d)


@register_tool(tags=["edit"])
@tool
def search_file(search_term: str, file_path: str = "") -> str:
    """在单文件里搜索（不给 file_path 则搜当前打开的文件），返回 行号:内容"""
    if file_path and _inside(file_path) is None:
        return "拒绝：路径越界"
    return current_editor().search_file(search_term, file_path or None)


@register_tool(tags=["edit"])
@tool
def find_file(file_name: str, dir_path: str = "./") -> str:
    """按文件名在目录树里查找，返回完整路径列表"""
    d = _inside(dir_path)
    return "拒绝：路径越界" if d is None else current_editor().find_file(file_name, d)
