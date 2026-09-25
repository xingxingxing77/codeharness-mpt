"""FakeLLM：按剧本吐回复，所有单测用它（花不起真钱也跑得起测试）。"""
from contextlib import contextmanager
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
                payload = outer._next()
                m = schema.model_validate_json(payload)
                # structured 也走记账出口——真网关同款洞（「动态范式每轮思考不进账」）当年只修了
                # LLMGateway，FakeLLM 这半边一直静默记 0；t13 端到端门禁现形（S8 第十六处）。
                # ⚠ 正文必须是**那段 JSON**，不许是空串（B8）：真网关 json_schema 档回的正文就是
                # JSON（`LLMGateway.structured` 的 `_parse` 读的正是 `raw.content`）。给空串等于让
                # `cost.add_usage` 判成「花了钱没产出」⇒ 替身场子里**每一次结构化思考**都给
                # `empty_output_calls` +1。那比读到 0 更坏：它是一个**正向假读数**，而 §4 第 4 条
                # 禁的正是拿 FakeLLM 的读数当真结论。
                resp = AIMessage(content=payload)
                resp.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                outer.cost_manager.add_usage(resp, model="gpt-4o", tag=kw.get("tag", "structured"))
                return m

        return _Structured()



class DirEmbeddings:
    """按**方向**给定向量：每条文本与 query 的余弦由调用方指定，名次与分数都是门禁说了算。

    为什么 `HashEmbeddings` 不够用（C23）：它按字面算，于是「字面最像问题」的切片分最高——而那正是
    C23 要拦的形状。要钉「不相关的被挡在外面、相关的仍在里面」，得能把**语义分**与**字面重叠**拆开：
    dense 由这里的 cos 表给定，词法腿仍是生产件 `qdrant_store.sparse_from_text` 从文本现算。
    真 bge-m3 下的同一判据在 `tests/manual_recall_floor_curve.py`（C20 那把尺子）里量。
    """

    dim = 64

    def __init__(self, cos: dict[str, float], query: str):
        self.cos, self.query = cos, query

    def _v(self, text: str) -> list[float]:
        if text == self.query:
            return [1.0] + [0.0] * (self.dim - 1)
        c = self.cos[text]
        s = max(0.0, 1.0 - c * c) ** 0.5
        return [c, s] + [0.0] * (self.dim - 2)

    async def aembed_documents(self, texts):
        return [self._v(t) for t in texts]

    async def aembed_query(self, q):
        return self._v(q)


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


@contextmanager
def uncalibrated_embeddings():
    """把 C23 的相关性下限**显式关掉**，给那些「与本项无关、但用的不是标定过的那个 embedding」的格子。

    `RECALL_FLOOR__MIN_SCORE=0.40` 是按本机 bge-m3 的余弦刻度标定在 C20 那张尺子上的（依据写在
    `configs/settings.py` 的 `FLOOR_CALIBRATED_*`）；`HashEmbeddings` 是 bag-of-chars，两条中文文本
    只要有共同字符就能拿高分——那根线在这个刻度上量不出任何产品语义。
    **显式关比让每格自己撞红好，但撞红这件事本身留在账上**：这一版默认值翻开时，s5 的 t15/t16/t25
    与 s15 的 t1/t10 是真的先红过——那条红就是「换 embedding 模型必须重量这根线」不是文档空话的凭据。
    改默认值会先红在 s5 t34/t35 与 s15 t12（那三格才是这道闸自己的判据）。
    """
    from codeharness.configs.settings import settings
    cfg = settings.recall_floor
    keep = cfg.mode, cfg.memory_mode, cfg.min_score, cfg.max_rank, cfg.oversample
    cfg.mode = cfg.memory_mode = "off"        # C36：两腿各有一档，要关就都得关
    try:
        yield cfg
    finally:
        (cfg.mode, cfg.memory_mode, cfg.min_score, cfg.max_rank,
         cfg.oversample) = keep
