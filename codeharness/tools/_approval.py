"""工具审批的判定表（S11-批次36）。

三档 = **免审范围**：会话档 `permission` 覆盖不到的动作，在真正执行前挂起等人批。
    readonly         只读面免审；任何写文件 / 执行 / 联网都要批
    workspace_write  外加「写进本会话工作区」免审；终端、联网、越界写仍要批
    full_access      全免审

两条刻意的设计：
① 表放这里而不是给 Action/工具加类属性——`codeharness/actions/*.py` 与 `tools/libs/*` 是
   各自的改动面，一处中心表才不会漏项，也不制造跨文件耦合。
② 认不出的动作一律按 `full_access` 需要审批（fail-closed）；会话档认不出按 `readonly`
   （最严）。宁可多问一次，不可放行一个没登记的动作。
"""
import hashlib
import json

TIERS = ("readonly", "workspace_write", "full_access")
TIER_RANK = {name: i for i, name in enumerate(TIERS)}
PERMISSION_DEFAULT = "readonly"     # 会话档认不出时按最严处理：多问一次，不放行

# 工具名取自 TOOL_REGISTRY 全量视图（实测 18 个，tags 全为 None，所以只能按名字定档）
TOOL_TIER = {
    "read_file": "readonly", "open_file": "readonly", "goto_line": "readonly",
    "scroll_down": "readonly", "scroll_up": "readonly", "search_dir": "readonly",
    "search_file": "readonly", "find_file": "readonly",
    "write_file": "workspace_write", "create_file": "workspace_write",
    "append_file": "workspace_write", "edit_file_by_replace": "workspace_write",
    "insert_content_at_line": "workspace_write",
    "execute_shell_async": "full_access", "terminal_command": "full_access",
    "search_internet": "full_access", "git_create_pull": "full_access",
    "git_create_issue": "full_access",
}

# Action 自己就会把 Document 存进产物仓，所以「产出即写」是常态。这里**逐个显式定档**而不是
# 靠默认值兜：默认值要么 fail-open（新加的跑命令件被当成写文档放行）要么 fail-closed
# （经典线每份文档都弹一次，档位形同作废），两种都不对。新加 Action 不登记 → 门禁 t8 判红。
_ACTION_FULL = {
    # 会真跑命令 / 起解释器 / 联网 / 推外部系统 / 读会话工作区之外的目录
    "RunCode", "RunPythonCode", "WriteAnalysisCode", "ExecuteTask", "DebugError",
    "Research", "SearchAndSummarize", "UploadKB", "ImportRepo",
}
_ACTION_READONLY = {"TalkAction"}                       # 纯对话，零产物零执行
_ACTION_WORKSPACE = {                                   # 产出 Document / 写工作区文件
    "WritePRD", "WritePRDReview", "WriteTRD", "EvaluateTRD", "EvaluateFramework",
    "EvaluateAction", "WriteFramework", "WriteContent", "WriteDesign", "WriteDirectory",
    "WriteTasks", "WriteCode", "WriteCodeReview", "WriteCodePlanAndChange", "WriteTest",
    "WriteTeachingPlan", "SummarizeCode", "RebuildClassView", "FixBug", "PrepareDocuments",
    "ExtractReadMe", "Pic2Txt", "InvoiceOCR", "DetectInteraction", "CompressExternalInterfaces",
}
ACTION_TIER = {**{n: "readonly" for n in _ACTION_READONLY},
               **{n: "workspace_write" for n in _ACTION_WORKSPACE},
               **{n: "full_access" for n in _ACTION_FULL}}

# 写类工具的路径参数名不统一（Editor 面三件各叫一个），逐个试
_PATH_KEYS = ("path", "filename", "file_name", "file_path", "dir_path")


def args_digest(args: dict) -> str:
    return hashlib.sha1(json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                                   default=str).encode("utf-8")).hexdigest()[:12]


def approval_id(sid: str, node: str, name: str, args: dict) -> str:
    """稳定可重算的待批标识。gate 节点在 resume 后会被**整节点重放**（plan_and_act.py:6-7），
    靠这个 id 命中决策台账才不会重复问、也不会把已批的动作再挂起一次。"""
    return hashlib.sha1(f"{sid}|{node}|{name}|{args_digest(args)}".encode("utf-8")).hexdigest()[:16]


def writes_inside_workspace(args: dict) -> bool:
    """写类动作是否落在本会话工作区内。复用的就是工具面唯一那条路径判据（`_boundary`，
    两个文件面共用），另写一份判据迟早长歪（门禁 t2 当年钉过 startswith 放行兄弟目录的坑）。"""
    from codeharness.tools._boundary import safe_session_path
    for key in _PATH_KEYS:
        v = (args or {}).get(key)
        if isinstance(v, str) and v:
            return safe_session_path(v) is not None
    return True        # 不带路径的写类件，按表里定的档走


def required_tier(name: str, args: dict | None = None, kind: str = "tool") -> str:
    """kind: tool = RoleZero 工具面的命令名；action = 经典线的 Action 类名。"""
    if kind == "action":
        base = ACTION_TIER.get(name, "full_access")
    else:
        base = TOOL_TIER.get(name, "full_access")
    if base == "workspace_write" and not writes_inside_workspace(args or {}):
        return "full_access"        # 越界写比工作区内写高一档
    return base


def needs_approval(permission: str, required: str) -> bool:
    return TIER_RANK.get(required, len(TIERS) - 1) > TIER_RANK.get(permission, 0)


def preview(name: str, args: dict) -> str:
    """审批卡上那一行原文：命令 > 路径 > 查询 > 整包截断。"""
    args = args or {}
    for key in ("command", "cmd", *_PATH_KEYS, "query", "question", "content"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return f"{name}: {v.strip()[:200]}"
    return f"{name}: {json.dumps(args, ensure_ascii=False, default=str)[:200]}"


ALLOW_ONCE = "allowed-once"
REJECTED = "rejected"


def gate_decide(name: str, args: dict, *, node: str, io_, reason: str = "",
                kind: str = "tool", permission: str | None = None) -> tuple[str | None, dict | None]:
    """gate 节点唯一的判定入口，返回 `(decision, item)`：

    - `("allowed"|"rejected", None)` —— 已有结论，直接照办；
    - `(None, item)` —— 要问人，调用方 `interrupt(item)` 挂起。

    `sid` 从注入的 `io_` 上取（`io_.sid`）：内核不 import server，`SESSION_ID` 那个 ContextVar
    在 `server/bridges.py`，而适配器本来就是 runner 按会话构造的，天然带 sid。

    两条不算 fail-open 的放行：① 档位覆盖得到；② `io_` 没装（内核直跑图，门禁与离线测试走这条）
    ——那时没有任何人类通道，挂起等于永久卡死，所以放行而不是拦。生产路径由 `runner._session_ctx`
    必定装载 `io_`，缺装载只可能出现在 server 之外。
    """
    required = required_tier(name, args, kind=kind)
    if permission is None:
        # 没显式传档就读 ContextVar（runner._session_ctx 按会话装的）——直接兜默认值会让
        # 服务端装的档位永远不生效，full_access 的会话也被当成只读拦（2026-09-20 s7 t13 抓到）
        from codeharness.runtime import PERMISSION
        permission = PERMISSION.get()
    tier = permission if permission in TIER_RANK else PERMISSION_DEFAULT
    if not needs_approval(tier, required):
        return "allowed", None
    if io_ is None:
        return "allowed", None
    aid = approval_id(getattr(io_, "sid", "") or "", node, name, args)
    decided = io_.decision(aid)
    if decided is not None:
        return ("allowed" if decided == ALLOW_ONCE else "rejected"), None
    return None, {"id": aid, "tool": name, "kind": kind, "args_preview": preview(name, args),
                  "reason": reason, "tier_required": required, "tier_session": tier, "node": node}
