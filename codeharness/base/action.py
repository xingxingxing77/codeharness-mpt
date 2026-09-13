"""动作基类。改造自源 actions/action.py:29-119：
剥 SerializationMixin/ContextMixin/ActionNode/私有 LLM；保留 name/prefix/_aask/_format_history/run 约定；
新增 output_schema（with_structured_output 替代 ActionNode）。"""
from typing import ClassVar, Optional, Type
from pydantic import BaseModel, Field


class BaseAction(BaseModel):
    name: str = ""
    desc: str = ""
    prefix: str = ""                                   # system prompt（Agent.build_prefix 灌入）
    output_schema: ClassVar[Optional[Type[BaseModel]]] = None   # 类级常量（子类覆盖无需注解）
    llm: Optional[object] = None                       # LLMGateway / FakeLLM，组队时注入

    def model_post_init(self, __ctx):
        if not self.name:
            self.name = type(self).__name__            # 对齐源 set_name_if_empty(:70)

    def _format_history(self, msgs: list) -> str:
        """逐字保留源 _run_action_node(:106-107) 的拼接语法"""
        return "## History Messages\n" + "\n".join(
            f"{idx}: {i}" for idx, i in enumerate(reversed(msgs)))

    async def _aask(self, prompt: str, system_msgs: Optional[list[str]] = None) -> str:
        msgs = ([self.prefix] if self.prefix else []) + (system_msgs or [])
        return await self.llm.aask(prompt, system_msgs=msgs or None, tag=self.name)

    async def _structured(self, prompt: str):
        """结构化输出：output_schema 为空则退化为纯文本"""
        if not self.output_schema:
            return await self._aask(prompt)
        return await self.llm.structured(self.output_schema).ainvoke(prompt)

    async def run(self, *args, **kwargs):
        raise NotImplementedError("子类必须实现 run")
