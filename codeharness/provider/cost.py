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
        """从响应的 response_metadata.token_usage 取用量。

        ⚠ 流式调用必须让底层 ChatOpenAI 带 `stream_usage=True`，否则末块不回 usage，
        这里拿到 (0, 0) 后在 `update_cost` 首行 early return——整条线跑完账上是 0。"""
        usage = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
        pt, ct = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
        self.update_cost(pt, ct, model)
        self.records.append({"tag": tag, "model": model, "pt": pt, "ct": ct})


class TokenCostManager(CostManager):
    """本地/免费模型：只记 token 不算钱。"""

    def update_cost(self, prompt_tokens, completion_tokens, model):
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
