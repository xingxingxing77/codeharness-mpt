"""经验序列化。判定 `复`（源 exp_pool/serializers/{base,simple}.py）+ `改`（RoleZeroSerializer）。

Base/Simple 逐字照源：req/resp 一律收成 str——经验池里只存字符串，roundtrip 才谈得上逐字段相等。
RoleZeroSerializer 的裁剪对象换了：源只留 "Command Editor.read executed: file_path" 那类工具输出
（它的 cmd 状态在消息流里），本仓 RoleZero 每轮的完整状态（计划进度、当前任务、语言）都在
**最后一条 human 消息（CMD_PROMPT）**里，system 人设与记忆窗口整体不进键（它们变了不代表"这是另一个
问题"，源同理：system 也进不了它的过滤器）。C170 起这条腿再往里裁：键 = 该消息的**决策身份段**
（Current Plan + Current Task），静态指导与每轮在变的 experience 槽都不进——理由与代价写在类 docstring。
"""
from abc import ABC, abstractmethod
from typing import Any, ClassVar

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
    resp = ZeroThought 的 model_dump_json（无损 roundtrip，验证在 _think 侧做）。

    C170：**键只取决策身份**（`# Current Plan` 起、到 `# Response Language` 止的那一段 = plan_status +
    current_task），不再拿整份渲染后的 CMD_PROMPT 当键。整份里三段都在污染这把尺子：
    - 尾部十几行静态指导（prompts/role_zero.py:68-88）逐字不变且字数最大 ⇒ 任何两个问题的向量都被它
      拉到一起，C164 量出的 0.901~0.935 危险带（跨角色/跨任务 CMD_PROMPT）就是它的产物；
    - `{current_state}` 在产线路径上**恒填字符串 "ready"**（role_zero.py:380）⇒ 进键零信息；
    - `{experience}` 是长期记忆每轮召回的措辞 ⇒ 同一任务同一步，召回换个说法键就变，跨场复用不成立
      （C164 那 15/15 全中靠的是题面逐字没变）。
    刻意**不带**的两段：`Past Experience`（理由同上，且它是「记忆」不是「这是不是同一个决策」）与
    `Response Language`+静态尾（语言与指导变了不代表决策变了）。代价写在账上：新记忆该改变决策时
    键不再改变 ⇒ 同一 (plan, task) 会冻在第一次的决策上，直到计划推进——那正是「复用」的定义，
    不是缺陷，但边界要说明白。
    认不出模板的输入（裸串、别的 prompt）**整段照旧进键**：宁可键大而准，也不造空键。"""

    PLAN_MARK: ClassVar[str] = "# Current Plan"        # 段起点（含标头，标头自己也是稳定分隔符）
    LANG_MARK: ClassVar[str] = "# Response Language"   # 段终点——模板里紧随 Current Task 之后

    def serialize_req(self, **kwargs) -> str:
        req = kwargs.get("req") or ""
        if isinstance(req, str):
            return self._decision_key(req)
        for m in reversed(list(req)):
            if getattr(m, "type", None) == "human" or getattr(m, "role", None) == "user":
                return self._decision_key(str(m.content))
        return str(req)          # 没有 human 消息：整体退 str()，宁可键大而准也不造空键

    @classmethod
    def _decision_key(cls, content: str) -> str:
        i, j = content.find(cls.PLAN_MARK), content.find(cls.LANG_MARK)
        return content[i:j].strip() if 0 <= i < j else content
