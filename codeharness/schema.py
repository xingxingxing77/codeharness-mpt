"""核心数据模型：Message / MessageQueue / Artifact / TeamState / Command。

完整定义见 docs/02-数据模型层.md §3-§4（基础五件套），
以及 docs/12-SOP完整流水线与Action库.md §3（CodingContext / TestingContext /
RunCodeContext / CodePlanAndChangeContext 四个上下文模型）。

 TODO(02)：按 docs/02 §3 骨架实现 Message（含 send_to serializer、
 instruct_content 简化为 dict+instruct_schema），再补 MessageQueue/Artifact/TeamState/Command。
"""
