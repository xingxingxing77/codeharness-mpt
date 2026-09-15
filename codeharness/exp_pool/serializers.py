"""经验序列化。判定 `复`（源 exp_pool/serializers/{base,simple}.py）+ `改`（RoleZeroSerializer）。

Base/Simple 逐字照源：req/resp 一律收成 str——经验池里只存字符串，roundtrip 才谈得上逐字段相等。
RoleZeroSerializer 的裁剪对象换了：源只留 "Command Editor.read executed: file_path" 那类工具输出
（它的 cmd 状态在消息流里），本仓 RoleZero 每轮的完整状态（计划进度、当前任务、语言）都在
**最后一条 human 消息（CMD_PROMPT）**里，所以键 = 最后一条 human 的 content，system 人设与
记忆窗口整体不进键（它们变了不代表"这是另一个问题"，源同理：system 也进不了它的过滤器）。
"""
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseSerializer(BaseModel, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    def serialize_req(self, **kwargs) -> str:
        """Serializes the request for storage.

        Do not modify kwargs. If modification is necessary, use copy.deepcopy to create a copy first.
        """

    @abstractmethod
    def serialize_resp(self, resp: Any) -> str:
        """Serializes the function's return value for storage."""

    @abstractmethod
    def deserialize_resp(self, resp: str) -> Any:
        """Deserializes the stored response back to the function's return value."""


class SimpleSerializer(BaseSerializer):
    """Just use `str` —— 照源。被装饰函数本来就返回 str 时零损耗。"""

    def serialize_req(self, **kwargs) -> str:
        return str(kwargs.get("req", ""))

    def serialize_resp(self, resp: Any) -> str:
        return str(resp)

    def deserialize_resp(self, resp: str) -> Any:
        return resp


class RoleZeroSerializer(SimpleSerializer):
    """RoleZero think 的收发都收在字符串上：req = 最后一条 human（CMD_PROMPT）的内容；
    resp = ZeroThought 的 model_dump_json（无损 roundtrip，验证在 _think 侧做）。"""

    def serialize_req(self, **kwargs) -> str:
        req = kwargs.get("req") or ""
        if isinstance(req, str):
            return req
        for m in reversed(list(req)):
            if getattr(m, "type", None) == "human" or getattr(m, "role", None) == "user":
                return str(m.content)
        return str(req)          # 没有 human 消息：整体退 str()，宁可键大而准也不造空键
