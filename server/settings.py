"""server 层路径与默认 LLM（key 来源改为 codeharness settings——不再读 config2.yaml）。
这是对接前端 health/start 的关键（旧实现读 config2.yaml，新栈没有该文件）。"""
from pathlib import Path
from codeharness.configs.settings import settings as core_settings

SERVER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_ROOT.parent
WORKSPACE_ROOT = REPO_ROOT / "workspace"          # 与 codeharness workspace_root 统一为同一目录
DATA_DIR = SERVER_ROOT / "data"
SESSIONS_FILE = DATA_DIR / "sessions.json"


def load_llm_defaults() -> tuple[dict | None, str]:
    """从 codeharness settings 取默认 LLM。返回 (llm_dict, problem)——签名与前端依赖的健康检查一致"""
    key = (core_settings.llm.api_key or "").strip()
    if key in {"", "YOUR_API_KEY", "sk-"}:
        return None, ".env 中的 LLM__API_KEY 仍是占位符，请填入真实 API key"
    return {"api_key": key, "model": core_settings.llm.model,
            "base_url": core_settings.llm.base_url}, ""
