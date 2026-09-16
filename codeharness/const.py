
"""全局常量与配置定义文件,基础设施配置中心

来源：metagpt/const.py（路径段与路由 tag 逐字对齐,根目录名换 CODEHARNESS）。
一致性敏感：route() 的 SOP 表 key、Agent.watch 订阅集、Memory 的 cause_by 索引、
ArtifactStore 的产物目录,全部认这里定义的同一批字符串——任何一处手写不一致 = 静默路由失败。
"""
import os
from pathlib import Path
from loguru import logger
import codeharness

# 获取安装包的根目录
def get_codeharness_package_root():
    """Get the root directory of the installed package."""
    package_root = Path(codeharness.__file__).parent.parent
    logger.info(f"Package root set to {str(package_root)}")
    return package_root

def get_codeharness_root():
    """Get the project root directory."""
    # Check if a project root is specified in the environment variable
    project_root_env = os.getenv("CODEHARNESS_PROJECT_ROOT")
    if project_root_env:
        project_root = Path(project_root_env)
        logger.info(f"PROJECT_ROOT set from environment variable to {str(project_root)}")
    else:
        # Fallback to package root if no environment variable is set
        project_root = get_codeharness_package_root()
        for i in (".git", ".project_root", ".gitignore"):
            if (project_root / i).exists():
                break
        else:
            project_root = Path.cwd()

    return project_root



# 项目目录结构路径常量
# 用户级配置目录
CONFIG_ROOT = Path.home() / ".codeharness"
# 总根，所有路径的起点
CODEHARNESS = get_codeharness_root()  # Dependent on CODEHARNESS_PROJECT_ROOT
# 默认工作区根目录
DEFAULT_WORKSPACE_ROOT = CODEHARNESS / "workspace"

# 示例与数据
EXAMPLE_PATH = CODEHARNESS / "examples"
EXAMPLE_DATA_PATH = EXAMPLE_PATH / "data"
DATA_PATH = CODEHARNESS / "data"
DABENCH_PATH = EXAMPLE_PATH / "di/InfiAgent-DABench/data"
EXAMPLE_BENCHMARK_PATH = EXAMPLE_PATH / "data/rag_bm"
TEST_DATA_PATH = CODEHARNESS / "tests/data"
SERDESER_PATH = DEFAULT_WORKSPACE_ROOT / "storage"  # TODO to store `storage` under the individual generated project

#  业务数据子目录
RESEARCH_PATH = DATA_PATH / "research"
TUTORIAL_PATH = DATA_PATH / "tutorial_docx"
INVOICE_OCR_TABLE_PATH = DATA_PATH / "invoice_table"

# 单元测试相关
UT_PATH = DATA_PATH / "ut"
SWAGGER_PATH = UT_PATH / "files/api/"
UT_PY_PATH = UT_PATH / "files/ut/"
API_QUESTIONS_PATH = UT_PATH / "files/question/"

# 源码与工具
TMP = CODEHARNESS / "tmp"
SOURCE_ROOT = CODEHARNESS / "codeharness"
PROMPT_PATH = SOURCE_ROOT / "prompts"
SKILL_DIRECTORY = SOURCE_ROOT / "skills"
TOOL_SCHEMA_PATH = CODEHARNESS / "codeharness/tools/schemas"
TOOL_LIBS_PATH = CODEHARNESS / "codeharness/tools/libs"

#  前端模板
TEMPLATE_FOLDER_PATH = CODEHARNESS / "template"
VUE_TEMPLATE_PATH = TEMPLATE_FOLDER_PATH / "vue_template"
REACT_TEMPLATE_PATH = TEMPLATE_FOLDER_PATH / "react_template"

# 消息路由 Tag
# 元数据字段 Key
MESSAGE_ROUTE_FROM = "sent_from"
MESSAGE_ROUTE_TO = "send_to"
MESSAGE_ROUTE_CAUSE_BY = "cause_by"
MESSAGE_META_ROLE = "role"

# 特殊路由目标
MESSAGE_ROUTE_TO_ALL = "<all>"
MESSAGE_ROUTE_TO_NONE = "<none>"
MESSAGE_ROUTE_TO_SELF = "<self>"   # Add this tag to replace `ActionOutput`

# 产物文件名（源 const.py:88-90）
REQUIREMENT_FILENAME = "requirement.txt"
BUGFIX_FILENAME = "bugfix.txt"
PACKAGE_REQUIREMENTS_FILENAME = "requirements.txt"

# 项目仓库 Repo 常量（源 const.py:92-112 逐字；值必须是 str——全项目用它拼路径）
DOCS_FILE_REPO = "docs"
PRDS_FILE_REPO = "docs/prd"
SYSTEM_DESIGN_FILE_REPO = "docs/system_design"
TASK_FILE_REPO = "docs/task"
CODE_PLAN_AND_CHANGE_FILE_REPO = "docs/code_plan_and_change"
COMPETITIVE_ANALYSIS_FILE_REPO = "resources/competitive_analysis"
DATA_API_DESIGN_FILE_REPO = "resources/data_api_design"
SEQ_FLOW_FILE_REPO = "resources/seq_flow"
SYSTEM_DESIGN_PDF_FILE_REPO = "resources/system_design"
PRD_PDF_FILE_REPO = "resources/prd"
TASK_PDF_FILE_REPO = "resources/api_spec_and_task"
CODE_PLAN_AND_CHANGE_PDF_FILE_REPO = "resources/code_plan_and_change"
TEST_CODES_FILE_REPO = "tests"
TEST_OUTPUTS_FILE_REPO = "test_outputs"
CODE_SUMMARIES_FILE_REPO = "docs/code_summary"
CODE_SUMMARIES_PDF_FILE_REPO = "resources/code_summary"
RESOURCES_FILE_REPO = "resources"
SD_OUTPUT_FILE_REPO = str(DEFAULT_WORKSPACE_ROOT)
GRAPH_REPO_FILE_REPO = "docs/graph_repo"
VISUAL_GRAPH_REPO_FILE_REPO = "resources/graph_db"
CLASS_VIEW_FILE_REPO = "docs/class_view"

# REAL CONSTS（源 const.py:114-166）
# 源 YAPI_URL / SD_URL（:114-115）是写死的内网与废弃服务地址，不搬。
DEFAULT_LANGUAGE = "English"
DEFAULT_MAX_TOKENS = 1500
COMMAND_TOKENS = 500
DEFAULT_TOKEN_SIZE = 500
BRAIN_MEMORY = "BRAIN_MEMORY"
SKILL_PATH = "SKILL_PATH"
SERPER_API_KEY = "SERPER_API_KEY"
BASE64_FORMAT = "base64"
REDIS_KEY = "REDIS_KEY"

# Message id（源 :132 值是 "0"；此前本文件用 "IGNORED_MESSAGE_ID" 哨兵，已对齐源值）
IGNORED_MESSAGE_ID = "0"

# Class Relationship（repo_parser / 类图产物用）
GENERALIZATION = "Generalize"
COMPOSITION = "Composite"
AGGREGATION = "Aggregate"

# Timeout
USE_CONFIG_TIMEOUT = 0  # Using llm.timeout configuration.
LLM_API_TIMEOUT = 300

# Assistant alias
ASSISTANT_ALIAS = "response"

# Markdown
MARKDOWN_TITLE_PREFIX = "## "

# Reporter（源名 METAGPT_REPORTER_DEFAULT_URL，环境变量前缀随栈改名）
REPORTER_DEFAULT_URL = os.environ.get("CODEHARNESS_REPORTER_URL", "")

# Metadata defines
AGENT = "agent"
IMAGES = "images"

# SWE agent（swe_agent_commands 目录目前判「弃」，此路径待接入 SWE 角色时才存在）
SWE_SETUP_PATH = get_codeharness_package_root() / "codeharness/tools/swe_agent_commands/setup_default.sh"

# experience pool
EXPERIENCE_MASK = "<experience>"

# TeamLeader's name
TEAMLEADER_NAME = "Mike"

DEFAULT_MIN_TOKEN_COUNT = 10000
DEFAULT_MAX_TOKEN_COUNT = 100000000


# ============ 新栈自有（源无对应物，全项目唯一真源，禁止在别处内联字面量） ============

class RequirementTag:
    """cause_by 消息 tag。源项目用 43 个 Action 类名当 tag（需 any_to_str 全家桶）；
    新栈收拢为纯字符串常量，序列化零成本。**字符串值必须与源 Action 类名逐字相等**，
    否则从源逐字复制过来的 watch 订阅会静默失配。"""

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


# 模块级别名：schema.py 的 cause_by 默认值
USER_REQUIREMENT = RequirementTag.USER_REQUIREMENT


class RepoName:
    """新栈产物目录的聚合入口。值一律引上面的 *_FILE_REPO 常量，避免两处真源。
    ArtifactStore.SUBDIRS 与全部 Action 的读写都从这里取。"""

    DOCS = DOCS_FILE_REPO
    PRD = PRDS_FILE_REPO
    SRC = "src"                                    # 源无对应：新栈产物源码目录
    TESTS = TEST_CODES_FILE_REPO
    TEST_OUTPUTS = TEST_OUTPUTS_FILE_REPO
    RESOURCES = RESOURCES_FILE_REPO


class DocName:
    """新栈产物文件名。与 cause_by 同样一致性敏感——WriteCode 读 design.md、
    DebugError 读 test_outputs 里的 output 文件，两处字符串不一致 = 静默查不到文件。"""

    REQUIREMENT = "requirement.md"
    PRD = "prd.json"
    PRD_MD = "prd.md"
    DESIGN = "design.md"
    DESIGN_JSON = "design.json"      # 源 system_design.json：机器读的真源；design.md 是人读替身
    TASKS = "tasks.json"
    CODE_SUMMARY = "code_summary.md"
    CODE_PLAN_AND_CHANGE = "code_plan_and_change.md"   # 源 CODE_PLAN_AND_CHANGE_FILE_REPO 的产物面
    # ⚠ docs 工单名只有 BUGFIX_FILENAME（"bugfix.txt"，生产链 write_prd→WriteCode 都吃它）。
    # 此处曾有 BUGFIX="bugfix.md" 死孪生——同物两值正是本类 docstring 警告的"静默查不到文件"，
    # B2b 接线时抓出删除（接线台账附带还的债）。
