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
                m = schema.model_validate_json(outer._next())
                # structured 也走记账出口——真网关同款洞（「动态范式每轮思考不进账」）当年只修了
                # LLMGateway，FakeLLM 这半边一直静默记 0；t13 端到端门禁现形（S8 第十六处）。
                resp = AIMessage(content="")
                resp.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                outer.cost_manager.add_usage(resp, model="gpt-4o", tag=kw.get("tag", "structured"))
                return m

        return _Structured()

class HashEmbeddings:
    """确定性 bag-of-chars 假 embedding：离线、可复现，dense 只看得见字符重叠（对照实验要利用的就是这点）。

    原来住在 `tests/s5_memory_rag.py`，C3 起 S5/S15 两边都要它（知识库那条链的门禁不能挂在
    真 embedding 服务上），所以搬进 fake 与 FakeLLM 作伴——**测试替身是产品树的公共件，
    不是一个门禁的私产**。bge-m3 那半格仍由 s5 t25 在真服务在线时补。"""

    dim = 64

    def _v(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for ch in text:
            v[ord(ch) % self.dim] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    async def aembed_documents(self, texts):
        return [self._v(t) for t in texts]

    async def aembed_query(self, q):
        return self._v(q)
