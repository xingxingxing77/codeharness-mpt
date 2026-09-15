"""用量计量：token 与成本的**只读**累计。

`Costs`/`CostManager.update_cost`/`TokenCostManager` 是计量本体；`add_usage` 是 LangChain 适配层；
`records` 供 trace 与前端用量面板读取。

这里没有任何预算判断：不设上限、不抛异常、不预测"这一问值不值得发"。
限额属于计费域，将来落在 HTTP 入口或网关外壳，不进 agent 内核。
"""
from typing import NamedTuple

from pydantic import BaseModel

from codeharness.logs import logger
from codeharness.provider.token_costs import TOKEN_COSTS


class Costs(NamedTuple):
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost: float


class CostManager(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost: float = 0
    token_costs: dict = TOKEN_COSTS
    records: list = []

    def update_cost(self, prompt_tokens, completion_tokens, model):
        if prompt_tokens + completion_tokens == 0 or not model:
            return
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        if model not in self.token_costs:
            logger.warning(f"Model {model} not found in TOKEN_COSTS, cost not counted.")
            return
        cost = (prompt_tokens * self.token_costs[model]["prompt"]
                + completion_tokens * self.token_costs[model]["completion"]) / 1000
        self.total_cost += cost
        logger.info(f"Total running cost: ${self.total_cost:.3f} | "
                    f"Current: ${cost:.3f}, pt={prompt_tokens}, ct={completion_tokens}")

    def get_costs(self) -> Costs:
        return Costs(self.total_prompt_tokens, self.total_completion_tokens, self.total_cost)

    def add_usage(self, resp, model: str = "", tag: str = ""):
        """取一次响应的用量，两个来源按新栈口径排优先级：

        1. `usage_metadata` —— LangChain 1.x 标准字段。`streaming=True` 建出来的 ChatOpenAI
           （本仓 `cfg.stream` 默认即 True）只往这里写，`token_usage` 是 None；
        2. `response_metadata.token_usage` —— OpenAI 原始口径，非流式构造时才有。

        只读第 2 条会让整条线跑完账上是 0（2026-09-15 真模型实测）。
        流式还须给底层带 `stream_usage=True`，否则末块连 usage 都不回。"""
        um = getattr(resp, "usage_metadata", None) or {}
        if um:
            pt, ct = um.get("input_tokens", 0) or 0, um.get("output_tokens", 0) or 0
        else:
            usage = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
            pt, ct = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
        if pt + ct == 0:
            # 漏账必须可见：update_cost 对 0 静默 return，真模型实测一场会话里几十次调用
            # 只有零星几笔入账时无从分辨"哪条路没回执"（2026-09-15）。有 warning 才有可 grep 的账差。
            logger.warning(f"add_usage: response 无 usage，本笔不进账 (model={model}, tag={tag})")
        self.update_cost(pt, ct, model)
        self.records.append({"tag": tag, "model": model, "pt": pt, "ct": ct})


class TokenCostManager(CostManager):
    """本地/免费模型：只记 token 不算钱。"""

    def update_cost(self, prompt_tokens, completion_tokens, model):
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
