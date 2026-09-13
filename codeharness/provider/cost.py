"""计费。Costs/CostManager 复制自源 utils/cost_manager.py（挂全局 Context 改为实例传入）；
add_usage 是 LangChain 适配层；records 供前端成本表。"""
from typing import NamedTuple, Optional
from pydantic import BaseModel
from codeharness.provider.token_costs import TOKEN_COSTS
from codeharness.logs import logger


class NoMoneyException(Exception):
    """预算耗尽（源 utils/exceptions.py 定义、team.py:98 抛出；新栈抛出点在 team_graph.budget_guard）"""


class Costs(NamedTuple):
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost: float
    total_budget: float


class CostManager(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_budget: float = 0
    max_budget: float = 10.0
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
        logger.info(f"Total running cost: ${self.total_cost:.3f} | Max budget: ${self.max_budget:.3f} | "
                    f"Current: ${cost:.3f}, pt={prompt_tokens}, ct={completion_tokens}")

    def get_costs(self) -> Costs:
        return Costs(self.total_prompt_tokens, self.total_completion_tokens, self.total_cost, self.total_budget)

    # ---- LangChain 适配层（源项目没有） ----
    def add_usage(self, resp, model: str = "", tag: str = ""):
        usage = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
        pt, ct = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
        self.update_cost(pt, ct, model)
        self.records.append({"tag": tag, "model": model, "pt": pt, "ct": ct})

    def check_budget(self):
        """预算把守（对齐 team._check_balance 语义），budget_guard 调用"""
        if self.total_cost >= self.max_budget > 0:
            raise NoMoneyException(f"Insufficient funds: ${self.max_budget}, used ${self.total_cost:.3f}")


class TokenCostManager(CostManager):
    """本地/免费模型：只记 token 不算钱（源 :94 同名类）"""
    def update_cost(self, prompt_tokens, completion_tokens, model):
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens