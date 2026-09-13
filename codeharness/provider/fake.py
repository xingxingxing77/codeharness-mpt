"""FakeLLM：按剧本吐回复，所有单测用它（花不起真钱也跑得起测试）。"""
from typing import Optional
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.i = 0
        self.calls = []                      # 断言用：每次调用的入参
        self.model_name = "fake"
        from codeharness.provider.cost import CostManager
        self.cost_manager = CostManager()

    def _next(self) -> str:
        r = self.responses[min(self.i, len(self.responses) - 1)]   # 耗尽后重复最后一条
        self.i += 1
        return r

    async def ainvoke(self, msgs, tag: str = "", **kw) -> AIMessage:
        self.calls.append(msgs)
        resp = AIMessage(content=self._next())
        resp.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        # 按 gpt-4o 价位记账（TOKEN_COSTS 保证有此行），否则 unknown model 不计价、链路测不到
        self.cost_manager.add_usage(resp, model="gpt-4o", tag=tag)
        return resp

    async def aask(self, prompt, system_msgs: Optional[list[str]] = None, tag: str = "") -> str:
        msgs = [SystemMessage(content=s) for s in (system_msgs or [])] + [HumanMessage(content=prompt)]
        return (await self.ainvoke(msgs, tag=tag)).content   # 走 ainvoke 才记账（与 LLMGateway.aask 同构）

    def astream_text(self, msgs, tag: str = ""):
        async def gen():
            for ch in self._next():
                yield ch
        return gen()

    def structured(self, schema: type[BaseModel]):
        outer = self

        class _Structured:
            async def ainvoke(self, prompt, **kw):
                outer.calls.append(prompt)
                return schema.model_validate_json(outer._next())

        return _Structured()