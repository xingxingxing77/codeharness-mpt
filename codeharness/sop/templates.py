"""SopTemplate（N7）：源 software_company.py(157) 把组队写死，平台把它变成可存取的对象。

模板 = assemble(llm)→agents 装配函数 + edges（cause_by→角色名的订阅表）+ 说明性 name/desc。
内置 classic_sop 直接引 team.classic_team 与 team_graph.SOP——模板化不复制第二套组队，
对内置线只换表达不换实现（判定表：复/重 之间取"引用"）。"""
from typing import Callable

from pydantic import BaseModel, ConfigDict


class SopTemplate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    desc: str = ""
    assemble: Callable = None          # (llm) -> dict[str, role]
    edges: dict[str, list[str]] = {}   # cause_by tag -> 目标角色名（与 Agent.watch 成对，s3b t11）

    def build_agents(self, llm) -> dict:
        agents = self.assemble(llm)
        if not agents:
            raise ValueError(f"模板 {self.name} 装配出空角色表")
        return agents


# 扩展模板表（N7 对外面）：扩展线经 register_template 入库，不改内核即新增 SOP。
# 内置表每次现构造（它引函数不存状态），扩展表进程级——ext_api 是唯一正门（N2 约定）。
_EXT_TEMPLATES: dict[str, "SopTemplate"] = {}


def register_template(tpl: SopTemplate) -> SopTemplate:
    """注册扩展 SOP 模板。重名（含内置名）与非 SopTemplate 对象当场 raise，不静默吞。"""
    if not isinstance(tpl, SopTemplate):
        raise TypeError("SOP 模板必须是 SopTemplate 实例")
    if tpl.name in _EXT_TEMPLATES or tpl.name in builtin_templates():
        raise ValueError(f"SOP 模板 {tpl.name!r} 已存在，不许覆盖")
    _EXT_TEMPLATES[tpl.name] = tpl
    return tpl


def builtin_templates() -> dict[str, SopTemplate]:
    """内置模板表 + 扩展模板表（新角色线/垂直 SOP：内置在此追加，扩展走 register_template）。"""
    from codeharness.environment.team_graph import SOP
    from codeharness.team import classic_team
    tpls = {
        "classic_sop": SopTemplate(
            name="classic_sop",
            desc="软件公司经典流水线：需求→PRD→设计→任务→代码→测试→沙箱真跑",
            assemble=classic_team,
            edges={k: list(v) for k, v in SOP.items()},
        ),
    }
    tpls.update(_EXT_TEMPLATES)
    return tpls
