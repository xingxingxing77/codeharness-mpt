"""全局常量与消息路由 tag。

内容依据 docs/01-工程骨架.md §2.3 与 docs/02-数据模型层.md §3（RequirementTag）。
"""

# ---- 消息路由（对齐 metagpt/const.py 与 schema.py 的路由语义）----
MESSAGE_ROUTE_TO_ALL = "*"        # 广播标记（schema.py:241 依赖）
MESSAGE_ROUTE_TO_SELF = "<self>"  # 自投递：write→review→summary 内环不广播（const.py:83，见 docs/12 §2）
IGNORED_MESSAGE_ID = "IGNORED_MESSAGE_ID"  # memory.py 去重语义依赖（docs/04）

# ---- workspace / 存档 ----
WORKSPACE_ROOT = "workspace"      # 产物落盘根目录（ArtifactStore，docs/14 §1）
SERDESER_PATH = "storage"         # 兼容语义，checkpointer 落地后可删


class RequirementTag:
    """cause_by 消息 tag：全项目约定为纯字符串，不用类对象（docs/02 §5 坑点）。

    对齐源项目 actions/add_requirement.py 等 tag 定义。
    """

    USER_REQUIREMENT = "UserRequirement"
    WRITE_PRD = "WritePRD"
    WRITE_PRD_REVIEW = "WritePRDReview"
    WRITE_DESIGN = "WriteDesign"
    WRITE_TASKS = "WriteTasks"
    WRITE_CODE_PLAN_AND_CHANGE = "WriteCodePlanAndChange"
    WRITE_CODE = "WriteCode"
    WRITE_CODE_REVIEW = "WriteCodeReview"
    SUMMARIZE_CODE = "SummarizeCode"
    WRITE_TEST = "WriteTest"
    RUN_CODE = "RunCode"
    DEBUG_ERROR = "DebugError"
    FIX_BUG = "FixBug"
