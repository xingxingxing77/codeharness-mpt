"""git 工具两件（源 tools/libs/git.py:131 的 `改`）。
源实现本体依赖未移植的 `metagpt.utils.git_repository`（判弃）与 PyGithub——死复制件带悬空
import，一接线即 ModuleNotFoundError（接线台账 #8 实锤）。本仓改走 `gh` CLI（子进程，零新
Python 依赖）：gh 不在场返回降级文案而不抛（工具面给模型的必须是可读结果）；凭据由 gh 自身
登录态持有，源读 ~/.git-credentials 的取 token 路径不搬（源码里 token 变量还有未命中即
NameError 的隐患）。返回形态 PullRequest/Issue 对象随 PyGithub 弃，统一 str。
函数名与参数面照源（app_name/issue 两项在源体内不参与请求，弃并记录）。"""
from langchain_core.tools import tool

from codeharness.logs import logger
from codeharness.tools.sandbox import run_proc
from codeharness.tools.tool_registry import register_tool


async def _gh(args: list[str]) -> str:
    try:
        r = await run_proc(args, timeout=60)
    except FileNotFoundError:
        return "[git 工具不可用：未找到 gh CLI。安装 gh 并 `gh auth login`，或由人工在托管平台操作]"
    out = (r.stdout + r.stderr).strip()
    if r.return_code != 0:
        return f"[gh 失败 rc={r.return_code}] {out[:2000]}"
    return out[:2000]


@register_tool(tags=["git"])
@tool
async def git_create_pull(base: str, head: str, base_repo_name: str, head_repo_name: str = "",
                          title: str = "", body: str = "") -> str:
    """在托管仓库创建 Pull Request（走 gh CLI），返回 PR 链接或降级说明。
    base_repo_name 形如 "user/repo"，跨仓 PR 时 head_repo_name 填 fork 的 "user/repo"。
    关键词：提交代码、推上去、推到远端、开 PR、合并请求、让人评审、提审、branch、pull request。
    示例：git_create_pull(base="main", head="fix-login", base_repo_name="acme/app")"""
    args = ["gh", "pr", "create", "--repo", base_repo_name, "--base", base,
            "--head", f"{head_repo_name}:{head}" if head_repo_name and head_repo_name != base_repo_name else head,
            "--title", title or f"{head} -> {base}", "--body", body or "Created by Codeharness"]
    logger.info(f"git_create_pull {base_repo_name}:{head} -> {base}")
    return await _gh(args)


@register_tool(tags=["git"])
@tool
async def git_create_issue(repo_name: str, title: str, body: str = "") -> str:
    """在托管仓库创建 Issue（走 gh CLI），返回 issue 链接或降级说明；凭据用 gh 自身登录态，不进工具签名（R7）。
    repo_name 形如 "user/repo"。
    关键词：开单、开个单子、工单、待办、记个问题、缺陷跟踪、崩溃要有人跟、issue、ticket、bug。
    示例：git_create_issue(repo_name="acme/app", title="登录接口超时")"""
    return await _gh(["gh", "issue", "create", "--repo", repo_name, "--title", title, "--body", body or title])
