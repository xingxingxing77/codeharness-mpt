"""register_action：扩展动作的准入闸。

本仓没有全局动作表——动作随角色装配（registry/team/模板都是传实例列表），所以这里的
"注册"语义是**校验+回显**：继承 BaseAction 即自动获得 R2 validator 与 self._aask 记账口，
不合格的当场拒。可直接当类装饰器用。"""
from codeharness.base.action import BaseAction


def register_action(cls):
    if not (isinstance(cls, type) and issubclass(cls, BaseAction)):
        raise TypeError(f"扩展 Action 必须继承 BaseAction（收到 {cls!r}）")
    if not cls.__dict__.get("run"):
        raise TypeError(f"{cls.__name__} 未实现 run()")
    return cls
