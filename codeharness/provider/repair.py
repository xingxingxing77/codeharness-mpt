"""LLM 原始输出修复：RoleZero/structured 解析失败时的 self-heal（07 号册 §3 落地）。"""
import json
import re
from typing import Optional, Type
from pydantic import BaseModel


def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _fix_trailing_comma(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_json_block(text: str) -> str:
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return m.group(1) if m else text


def _fix_unclosed(text: str) -> str:
    return text + "]" if text.strip().startswith("[") and not text.strip().endswith("]") else text


def repair_llm_raw_output(raw: str, schema: Type[BaseModel]) -> Optional[BaseModel]:
    """升级式修复管线：每档是前一档的组合，覆盖 LLM 最常见的四种坏输出
    （裸围栏 / 尾逗号 / 围栏+尾逗号 / JSON 前后带说明文字）。"""
    candidates = (
        raw,
        _strip_fence(raw),
        _fix_trailing_comma(_strip_fence(raw)),
        _fix_trailing_comma(_extract_json_block(raw)),
        _fix_unclosed(_fix_trailing_comma(_strip_fence(raw))),
    )
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except Exception:
            continue
    return None