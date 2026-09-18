"""N2 扩展点（施工3 批5）：不改内核加角色/动作/工具/SOP模板的唯一正门。

约定：内核文件（roles/actions/tools/team/environment/sop）对外只读——扩展件一律经本包注册；
注册失败（重名/非 BaseAction/非法对象）当场 raise，不静默吞。"""
from codeharness.ext_api.actions import register_action
from codeharness.ext_api.roles import register_role
from codeharness.ext_api.tools import register_tool
from codeharness.sop.templates import register_template

__all__ = ["register_role", "register_action", "register_tool", "register_template"]
