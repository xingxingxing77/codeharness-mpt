"""动作基类。

保留约定：`name` / `prefix` / `_aask` / `_format_history` / `run`，以及 `output_schema`。

这里落地 **字段级定向重试**（整结构级 `with_structured_output` 的补强）：
一次问齐所有字段后，只把**缺失或为空**的字段挑出来、带上原始上下文再问一轮，
而不是把整份 schema 重发一次。区别在成本与成功率上都是实打实的：
14 字段的 PRD 只缺 2 个字段时，重发的 token 从"整份结构 + 全部上下文"降到"2 个字段 + 上下文"。

不引入 HTTP 回调、不引入私有 LLM 封装；`llm` 是 `LLMGateway` 或 `FakeLLM`，
两者都提供 `structured(schema).ainvoke(prompt)`。
"""
from typing import Any, ClassVar, Iterable, Optional, Type

from pydantic import BaseModel, Field, create_model

# 只补空字段的追问模板。刻意不做多轮闲聊，一次问齐。
_PATCH_TEMPLATE = """Only fill the following MISSING fields, based on the context.
Return a markdown JSON object containing ONLY these keys.

## Missing fields
{fields}

## Context
{context}
"""


def _is_empty(v: Any) -> bool:
    """None / 空串 / 纯空白 / 空容器 都算没填上。"""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) == 0
    return False


class BaseAction(BaseModel):
    name: str = ""
    desc: str = ""
    prefix: str = ""                                        # system prompt（Agent.build_prefix 灌入）
    output_schema: ClassVar[Optional[Type[BaseModel]]] = None      # 子类覆盖无需注解
    llm: Optional[object] = None                           # LLMGateway / FakeLLM，组队时注入
    max_field_retries: int = 2                             # 补空字段的最多轮数（可按 Action 调）

    def model_post_init(self, __ctx):
        if not self.name:
            self.name = type(self).__name__                 # 对齐 set_name_if_empty 约定

    def _format_history(self, msgs: list) -> str:
        """历史消息拼接（沿用既有语法，别改格式——prompt 逐字性依赖它）。"""
        return "## History Messages\n" + "\n".join(
            f"{idx}: {i}" for idx, i in enumerate(reversed(msgs)))

    async def _aask(self, prompt: str, system_msgs: Optional[list[str]] = None) -> str:
        msgs = ([self.prefix] if self.prefix else []) + (system_msgs or [])
        return await self.llm.aask(prompt, system_msgs=msgs or None, tag=self.name)

    # ---- 结构化输出 + 字段级定向重试 ----
    async def _structured(self, prompt: str):
        """按 `output_schema` 取结构化结果；空字段定向补问，最多 `max_field_retries` 轮。"""
        schema = self.output_schema
        if not schema:
            return await self._aask(prompt)

        obj = await self._ask(schema, prompt)
        for _ in range(self.max_field_retries):
            missing = self._empty_fields(schema, obj)
            if not missing:
                return obj
            patch = await self._ask(self._partial(schema, missing), self._patch_prompt(prompt, obj, missing))
            merged = self._merge(obj, patch)
            if merged is None:                              # 合并后校验不过，保留上一轮结果
                return obj
            obj = merged
        return obj

    async def _ask(self, schema: Type[BaseModel], prompt: str) -> BaseModel:
        """走网关的结构化通道。失败时由 gateway 的修复档兜底，仍失败则原样抛出。"""
        return await self.llm.structured(schema).ainvoke(prompt)

    @staticmethod
    def _empty_fields(schema: Type[BaseModel], obj: BaseModel) -> list[str]:
        """哪些字段没填上。以 schema 声明顺序返回，保证补问模板的确定性。"""
        data = obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)
        return [f for f in schema.model_fields if _is_empty(data.get(f))]

    @staticmethod
    def _partial(schema: Type[BaseModel], fields: list[str]) -> Type[BaseModel]:
        """按缺失字段造一个只含这些键的部分模型（都可选，允许部分补上）。"""
        defs = {f: (Optional[schema.model_fields[f].annotation], None) for f in fields}
        return create_model(f"{schema.__name__}Patch", **defs)

    @staticmethod
    def _merge(obj: BaseModel, patch: BaseModel) -> Optional[BaseModel]:
        """只覆盖补上的字段，空值不覆盖已有内容。"""
        updates = {k: v for k, v in patch.model_dump().items() if not _is_empty(v)}
        if not updates:
            return obj
        try:
            return type(obj).model_validate({**obj.model_dump(), **updates})
        except Exception:
            return None

    def _patch_prompt(self, prompt: str, obj: BaseModel, missing: list[str]) -> str:
        """补问上下文 = 原 prompt 尾部 + 已填字段，避免模型重做已完成部分。"""
        schema = self.output_schema
        fields = "\n".join(f'- "{m}" key containing {self._field_hint(schema, m)};' for m in missing)
        filled = {k: v for k, v in obj.model_dump().items() if not _is_empty(v)}
        return _PATCH_TEMPLATE.format(
            fields=fields, context=self._context_of(prompt, filled)) + "\nReturn ONLY the JSON object."

    @staticmethod
    def _field_hint(schema: Type[BaseModel], field: str) -> str:
        """字段类型提示，够用即可（不猜语义，避免污染原 prompt 的措辞）。"""
        ann = schema.model_fields[field].annotation
        return getattr(ann, "__name__", None) or str(ann)

    @staticmethod
    def _context_of(prompt: str, filled: dict) -> str:
        import json
        return f"{prompt[-3000:]}\n\n## Already filled (do not redo)\n{json.dumps(filled, ensure_ascii=False, default=str)}"

    async def run(self, *args, **kwargs):
        raise NotImplementedError("子类必须实现 run")
