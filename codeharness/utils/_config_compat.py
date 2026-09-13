"""config2 兼容垫片：file.py 等复制件里残留的 `config.*` 引用落到 codeharness settings。"""
from types import SimpleNamespace
from codeharness.configs.settings import settings

config = SimpleNamespace(
    omniparse=None,                                   # 源 config2.omniparse（已按排除清单不搬）
    workspace=SimpleNamespace(path=settings.workspace_root),
)


def get_env_default(key: str, default: str = "") -> str:
    """源 metagpt.tools.libs.get_env_default 同语义：环境变量取值兜底"""
    import os
    return os.environ.get(key, default)
