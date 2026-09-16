"""会话边界的唯一判据（S4 判 `新` 的出口之一）。

tools/__init__ 的文件工具与 libs/editor_tools 的编辑命令面共用——一条规则写两处，
改天加第三条命令时漏掉一处就是旁路（接线台账 #7 落地时从 __init__ 抽出，保持
`from codeharness.tools import _safe` 的既有引用面不变）。"""
from pathlib import Path

from codeharness.runtime import session_root


def safe_session_path(path: str) -> Path | None:
    """相对路径落在 session_root 下则返回解析结果，越界（.. 或绝对路径指到外面）返回 None。
    ⚠ 必须 is_relative_to：str.startswith 把兄弟目录 ws_probe 当 ws 之内放行（前缀命中，门禁 t2 实测）。"""
    root = session_root()
    target = (root / path).resolve()
    return target if target.is_relative_to(root) else None
