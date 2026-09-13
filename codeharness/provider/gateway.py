"""LLM 网关：全项目唯一与对话模型通话的入口。
API 面对齐源 provider/base_llm.py（aask:179/aask_batch:215/acompletion_text:256/with_model:324）。
向量模型不在这里——embeddings() 工厂返回 bge-m3（settings.embedding）。"""
import asyncio
from typing import AsyncIterator, Optional
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel


class LLMGateway:
    def __init__(self, cfg=None, cost_manager=None):
        from codeharness.configs.settings import settings
        from codeharness.provider.cost import CostManager
        self.cfg = cfg or settings.llm
        self._model = ChatOpenAI(model=self.cfg.model, api_key=self.cfg.api_key,
                                 base_url=self.cfg.base_url, temperature=self.cfg.temperature,
                                 max_tokens=self.cfg.max_tokens, streaming=True)
        self.cost_manager = cost_manager or CostManager()

    @staticmethod
    def embeddings():
        """bge-m3 embedding 工厂（1024 维，OpenAI 兼容端点）"""
        from codeharness.configs.settings import settings
        return OpenAIEmbeddings(model=settings.embedding.model, base_url=settings.embedding.base_url,
                                api_key=settings.embedding.api_key or "EMPTY")

    async def ainvoke(self, msgs: list[BaseMessage], tag: str = "") -> BaseMessage:
        resp = await self._model.ainvoke(msgs)
        self.cost_manager.add_usage(resp, model=self.cfg.model, tag=tag)
        return resp

    async def aask(self, prompt: str, system_msgs: Optional[list[str]] = None, tag: str = "") -> str:
        msgs = [SystemMessage(content=s) for s in (system_msgs or [])] + [HumanMessage(content=prompt)]
        return (await self.ainvoke(msgs, tag=tag)).content

    async def aask_batch(self, msgs: list[BaseMessage], tag: str = "") -> str:
        return (await self.ainvoke(msgs, tag=tag)).content

    async def astream_text(self, msgs: list[BaseMessage], tag: str = "") -> AsyncIterator[str]:
        async for chunk in self._model.astream(msgs):
            if chunk.content:
                yield chunk.content

    def structured(self, schema: type[BaseModel]):
        """返回一个 runnable：ainvoke(prompt/msgs) -> schema 实例（替代 876 行 ActionNode）"""
        return self._model.with_structured_output(schema)

    def with_model(self, model: str) -> "LLMGateway":
        clone = LLMGateway.__new__(LLMGateway)
        clone.cfg = self.cfg
        clone._model = self._model.bind(model=model)
        clone.cost_manager = self.cost_manager
        return clone