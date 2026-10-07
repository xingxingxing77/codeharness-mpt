"""上下文预算的单一尺（P1，不变量 1）：压缩/截断/额度换算的判据都读这里，别处不许自己
拿 len()/条数/字符跟预算比（读点由 `s13 t16` 的申报口常驻盯着）。

三个组装点（都经 `of()`，同源同值——不是三把尺，是同一把尺的三处读法）：
- `gateway._apply_compression`：`of(self.cfg, self.cost_manager)`——token 判据（闸/水位/keep）；
- `RoleZero.__init__`：`of(llm.cfg, llm.cost_manager, msg_window=…)`——条数窗口也归它
  （P2 的「条数 OR token 水位」双判据，两个口径都从这里取）；
- `runner._session_ctx`：`of(None, self.costs[sid])` 装进 `CURRENT_BUDGET`——工具层
  （clip 的 16 处额度换算）够不到 llm，经 ContextVar 读会话校准系数 k。

「预算未设 ⇒ 契约休眠」：`armed=False` 时所有判据退化为「不压」（保住 ADR-20260922-01 的
「显式选择」语义）；无会话上下文（`CURRENT_BUDGET` 默认 None）时 clip 换算恒等。
"""
from dataclasses import dataclass

from codeharness.configs.compress_msg_config import CompressType

# P2：T1 主动压缩的三闸与目标（已定数，不是可调参数——改这些数要先拍）。
T1_TRIGGER = 0.60          # 触发水位（storage 全量 token ÷ 预算）
T1_TARGET = 0.40           # 目标压回水位
T1_MIN_GAIN = 0.15         # 最小收益（水位下降的百分点；不够本就不压）
T1_MAX_PER_RUN = 8         # 每场 T1 次数上限


@dataclass(frozen=True)
class ContextBudget:
    """预算快照 + 活引用：token 侧字段取自组装那一刻的 cfg（cfg 是会话级副本，`_make_llm`
    model_copy 后不再变），k 经 `meter` 引用**现读**（P2 会话中校准后会变）。"""
    token_limit: int | None = None
    keep_tokens: int = 0
    armed: bool = False
    msg_window: int = 0
    meter: object = None            # CostManager（校准系数 k 的会话级家）；None ⇒ k=1

    @classmethod
    def of(cls, cfg=None, meter=None, msg_window: int = 0) -> "ContextBudget":
        from codeharness.configs.settings import settings
        limit = getattr(cfg, "context_length", None) or None
        thr = float(getattr(cfg, "compress_threshold", 0.8) or 0.8)
        ct = getattr(cfg, "compress_type", CompressType.NO_COMPRESS)
        return cls(
            token_limit=limit,
            keep_tokens=int((limit or 0) * thr),
            # 闸条件逐字取自 gateway 原实现：预算显式配上 且（策略非 NO_COMPRESS 或 阈值 < 1）
            armed=bool(limit) and (ct != CompressType.NO_COMPRESS or thr < 1.0),
            msg_window=int(msg_window or settings.memory_overflow_size),
            meter=meter,
        )

    @property
    def calibration_k(self) -> float:
        """会话校准系数（P0 契约：初值 1、夹 [0.5,4]、切模型即重置——写入口在
        `CostManager.note_calibration`）。现读 meter：装进来若是快照，P2 会话中校准完
        clip 还会用旧值。"""
        return float(getattr(self.meter, "calibration_k", 1.0) or 1.0)

    def should_compress(self, tokens: int) -> bool:
        """token 判据的唯一定义点（gateway 的闸口）：`armed=False`（契约休眠）恒 False。"""
        return self.armed and tokens > self.keep_tokens

    def water(self, tokens: int) -> float:
        """水位：storage 全量的本地 token 数 ÷ 预算。未设预算 ⇒ 0.0（契约休眠）。"""
        return (tokens / self.token_limit) if self.token_limit else 0.0

    def t1_triggered(self, water: float) -> bool:
        """T1 触发：契约通电 且 水位 ≥ 60%。（条数判据不在此——它不删、无闸。）"""
        return self.armed and water >= T1_TRIGGER

    def t1_gain_ok(self, before: float, after: float) -> bool:
        """收益闸：压完水位得降 ≥15 个百分点（不够本就是白摘白写，不压）。"""
        return (before - after) >= T1_MIN_GAIN

    def clip_quota(self, chars: int) -> int:
        """clip 的字符额度按 k 换算：**只收紧、不放宽**（k>1 ⇒ n/k；k≤1 ⇒ 原样；k=1 ⇒ 逐字不变）。
        放宽字符额度等于悄悄改上下文预算（`utils/text.py::clip` 的额度纪律同一条）。"""
        k = self.calibration_k
        return int(chars / k) if k > 1 else chars
