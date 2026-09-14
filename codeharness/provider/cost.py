"""计费。Costs/CostManager 复制自源 utils/cost_manager.py（挂全局 Context 改为实例传入）；
add_usage 是 LangChain 适配层；records 供前端成本表。"""
from typing import NamedTuple, Optional
from pydantic import BaseModel
from codeharness.provider.token_costs import TOKEN_COSTS
from codeharness.logs import logger


class NoMoneyException(Exception):
    """预算耗尽。

    源定义在 `utils/common.py:323`（⚠ 不是 utils/exceptions.py），抛出点是 `team.py:99-100`
    的 `Team._check_balance`，源用两个位置参数抛：`NoMoneyException(total_cost, "Insufficient funds: ...")`。
    新栈的抛出点在 `team_graph.budget_guard`。"""


class Costs(NamedTuple):
    """源 cost_manager.py:18 逐字。"""

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
        """源 `Team._check_balance`（team.py:99-100）逐字对齐：`>=` 即抛，两参形式。"""
        if self.total_cost >= self.max_budget:
            raise NoMoneyException(self.total_cost, f"Insufficient funds: {self.max_budget}")

    # ---- 新栈自有（源 CostManager 只有 update_cost/get_costs，**没有这两个方法**） ----
    # 为 N5「每用户每会话配额」服务：源是"事后抛"，平台需要"发问前判断值不值得发"。
    def is_within_budget(self, msgs: list = None, **kwargs) -> bool:
        """预测式判断：这一问发出去还会不会在预算内。`estimated_cost`（美元）优先。"""
        if self.max_budget <= 0:                       # 未设预算 = 不限
            return True
        estimated = kwargs.get("estimated_cost")
        if estimated is None:
            tokens = kwargs.get("new_messages_added")
            if not tokens and msgs:
                from codeharness.utils.token_counter import count_message_tokens
                try:
                    tokens = count_message_tokens(
                        messages=[{"role": getattr(m, "role", "user"), "content": getattr(m, "content", str(m))}
                                  for m in msgs],
                        model=self._last_model or "gpt-4o")
                except Exception:                     # 价目表缺项不该中断预算判断，退回字符粗估
                    tokens = sum(len(getattr(m, "content", str(m))) for m in msgs) // 4
            if not tokens:
                return True
            rate = (self.token_costs.get(self._last_model or "gpt-4o", {}) or {}).get("completion", 0)
            estimated = tokens * rate / 1000
        # 已超支则一律不再发（哪怕预估 0）——与 check_budget 的 `>=` 语义保持一致
        return self.total_cost < self.max_budget and self.total_cost + float(estimated) < self.max_budget

    def update_budget(self, budget_used: float):
        """累计已授权的花费（源无此方法；新栈用它跟踪 budget 上限内的预扣额）。"""
        self.total_budget += budget_used

    @property
    def _last_model(self) -> str:
        return self.records[-1]["model"] if self.records else ""


class TokenCostManager(CostManager):
    """本地/免费模型：只记 token 不算钱（源 :94 同名类）"""
    def update_cost(self, prompt_tokens, completion_tokens, model):
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens