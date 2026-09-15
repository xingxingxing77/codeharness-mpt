"""config2 兼容垫片：file.py 等复制件里残留的 `config.*` 引用落到 codeharness settings。"""
from types import SimpleNamespace
from codeharness.configs.settings import settings

config = SimpleNamespace(
    omniparse=None,                                   # 源 config2.omniparse（已按排除清单不搬）
    workspace=SimpleNamespace(path=settings.workspace_root),
)


async def get_env_default(key: str, app_name: str = None, default_value: str = "") -> str:
    """源 `tools/libs/env.py` 的 `get_env_default` 等价物：按 app 读环境变量，取不到给默认值。

    源那套 `get_env`/`set_get_env_entry` 是 158 行的 app-key 注册表；本平台配置统一走
    pydantic-settings 的 `APP__KEY` 双下划线口径（R7），所以只留这一个读口，注册表判 `弃`。
    ⚠ 必须 async 且关键字名与调用点一致——`utils/file.py:154` 就写死了
    `await get_env_default(key=, app_name=, default_value=)`，签名漂了就是一条 TypeError。
    """
    import os

    name = f"{app_name.upper()}__{key.upper()}" if app_name else key.upper()
    return os.environ.get(name, default_value)
