"""register_role：把新角色工厂注入注册表（build_role/ALL_ROLES 立即可见）。

返回 unregister 闭包——测试/插件热卸载用；内核同名件拒绝覆盖（静默盖掉内置角色
= 用户脚本改变了平台的默认行为，这必须是不允许的）。"""
from codeharness.roles.registry import ALL_ROLES


def register_role(name: str, factory):
    """factory(llm, **kw) → 角色对象（Agent/RoleZero 或任何提供 as_node(name) 的等价件）。"""
    if name in ALL_ROLES:
        raise ValueError(f"角色 {name!r} 已存在（内置件不可被覆盖；换行为请起新名或经 SOP 模板重装配）")
    if not callable(factory):
        raise TypeError(f"factory 必须可调用，收到 {type(factory).__name__}")
    ALL_ROLES[name] = factory

    def unregister():
        if ALL_ROLES.get(name) is factory:
            del ALL_ROLES[name]
    return unregister
