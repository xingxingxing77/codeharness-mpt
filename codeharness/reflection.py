"""自反思：源 utils/reflection.py 语义——失败重复出现时，让 LLM 复盘历史并给出改写建议，
注入下一轮的 experience（RoleZero._think 的 experience 槽）。"""
import json
from codeharness.logs import logger

REFLECT_PROMPT = """You are self-reflecting on a task that keeps failing.
Task: {task}
Recent history (thoughts, commands, results): {history}
Recurring error: {error}

Produce a concise change-of-approach plan (<=150 words): what you tried, why it fails,
and ONE concrete different approach to try next. Do not repeat previous commands."""


async def reflect(llm, task: str, history: list, error: str) -> str:
    """返回改写建议文本（调用方拼进下一轮 CMD_PROMPT 的 experience 槽）"""
    advice = await llm.aask(REFLECT_PROMPT.format(
        task=task[:500], history=json.dumps(history[-3:], ensure_ascii=False, default=str)[:3000],
        error=error[:300]), tag="reflection")
    logger.info(f"reflection: {advice[:100]}...")
    return f"[Self-Reflection] {advice}"


def detect_repeated_error(history: list, window: int = 2) -> str | None:
    """最近 window 轮出现同名错误 → 返回该错误名（触发 reflect 的判据）"""
    errs = []
    for h in history[-window:]:
        for r in h.get("results", []):
            if r.get("result", "").startswith("[错误]"):
                errs.append(r["result"].split(":")[0])
    if len(errs) >= window and len(set(errs[-window:])) == 1:
        return errs[-1]
    return None
