"""全局常量与消息路由 tag —— 全项目的"字符串地址簿"。

文档集：README.md（总入口）、01–10 步施工文档（每步=手册+最终代码）、参考-判定与范围速查.md（判定依据）。
route() 的 SOP 表 key、Agent.watch 订阅集、Memory 的 cause_by 索引、产物文件读写，
全部认这里定义的同一批字符串——任何一处手写与这里不一致 = 静默路由失败 / 查不到文件。
"""

# ---- 消息路由（对齐 metagpt/const.py:77-83 与 schema.py:241 的路由语义）----
MESSAGE_ROUTE_TO_ALL = "*"        # 广播标记：Message.send_to 默认值
MESSAGE_ROUTE_TO_SELF = "<self>"  # 自投递：Engineer/QA 的 write→review、run→debug 内环不广播
IGNORED_MESSAGE_ID = "IGNORED_MESSAGE_ID"  # memory.py ignore_id 去重模式的哨兵 id

# ---- workspace / 存档 ----
WORKSPACE_ROOT = "workspace"      # 产物落盘根目录（settings.workspace_root 可覆盖）
SERDESER_PATH = "storage"         # 兼容语义，checkpointer 落地后可删


class RequirementTag:
    """cause_by 消息 tag。源项目用 43 个 Action 类名当 tag（需 any_to_str 全家桶）；
    新栈收拢为纯字符串常量，序列化零成本。"""

    USER_REQUIREMENT = "UserRequirement"          # 整条流水线的第一张多米诺：用户输入
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
    RUN_COMMAND = "RunCommand"                    # RoleZero 命令产物的 cause_by（第 7 步）


# 模块级别名：schema.py 的 cause_by 默认值（第 2 步）与文档 01 §1.4 的约定一致
USER_REQUIREMENT = RequirementTag.USER_REQUIREMENT


class RepoName:
    """产物目录约定：对齐源 const.py:90-109 的 15 个 *_FILE_REPO，收敛为
    ArtifactStore.SUBDIRS 的一处引用（impl/07）。"""

    DOCS = "docs"
    PRD = "docs/prd"
    SRC = "src"
    TESTS = "tests"
    TEST_OUTPUTS = "test_outputs"
    RESOURCES = "resources"


class DocName:
    """产物文件名：与 cause_by 同样一致性敏感——WriteCode 读 design.md、DebugError 读
    test_outputs 里的 output 文件，两处字符串不一致 = 静默查不到文件。
    全部 Action 必须从这里引用，禁止内联字面量。"""

    REQUIREMENT = "requirement.md"
    PRD = "prd.json"
    PRD_MD = "prd.md"
    DESIGN = "design.md"
    TASKS = "tasks.json"
    CODE_SUMMARY = "code_summary.md"
    BUGFIX = "bugfix.md"

# ---- utils/common 复制件依赖（源 const.py 逐字） ----
MARKDOWN_TITLE_PREFIX = "## "   # common.py:48 的 Markdown 标题前缀
