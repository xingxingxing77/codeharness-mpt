"""Experience schema。判定 `复`（源 exp_pool/schema.py 77 行）：字段名与序列化格式逐字照源——
「序列化 roundtrip 逐字段相等」钉的就是这份格式。常量 MAX_SCORE / DEFAULT_SIMILARITY_TOP_K /
LOG_NEW_EXPERIENCE_PREFIX 照抄，S6 复制进来的源侧 prompt/代码按这些名字取值。"""
import time
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

MAX_SCORE = 10

DEFAULT_SIMILARITY_TOP_K = 2

LOG_NEW_EXPERIENCE_PREFIX = "New experience: "


class QueryType(str, Enum):
    """Type of query experiences."""

    EXACT = "exact"
    SEMANTIC = "semantic"


class ExperienceType(str, Enum):
    """Experience Type."""

    SUCCESS = "success"
    FAILURE = "failure"
    INSIGHT = "insight"


class EntryType(Enum):
    """Experience Entry Type."""

    AUTOMATIC = "Automatic"
    MANUAL = "Manual"


class Score(BaseModel):
    """Score in Metric.（LLM 评分按判定推迟到终验后；入库时留 None 不造数据。）"""

    val: int = Field(default=1, description="Value of the score, Between 1 and 10, higher is better.")
    reason: str = Field(default="", description="Reason for the value.")


class Metric(BaseModel):
    """Experience Metric."""

    time_cost: float = Field(default=0.000, description="Time cost, the unit is milliseconds.")
    money_cost: float = Field(default=0.000, description="Money cost, the unit is US dollars.")
    score: Score = Field(default=None, description="Score, with value and reason.")


class Trajectory(BaseModel):
    """Experience Trajectory.（源由 ActionNode 线使用，本仓接线只走 req/resp 对。）"""

    plan: str = Field(default="", description="The plan.")
    action: str = Field(default="", description="Action for the plan.")
    observation: str = Field(default="", description="Output of the action.")
    reward: int = Field(default=0, description="Measure the action.")


class Experience(BaseModel):
    """Experience."""

    req: str = Field(..., description="")
    resp: str = Field(..., description="The type is string/json/code.")
    metric: Optional[Metric] = Field(default=None, description="Metric.")
    exp_type: ExperienceType = Field(default=ExperienceType.SUCCESS, description="The type of experience.")
    entry_type: EntryType = Field(default=EntryType.AUTOMATIC, description="Type of entry: Manual or Automatic.")
    tag: str = Field(default="", description="Tagging experience.")
    traj: Optional[Trajectory] = Field(default=None, description="Trajectory.")
    timestamp: Optional[float] = Field(default_factory=time.time)
    uuid: Optional[UUID] = Field(default_factory=uuid4)

    def rag_key(self):
        return self.req
