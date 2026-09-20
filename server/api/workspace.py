"""工作区文件树/读取。路径基准 = session.workspace = runtime.session_root()——
工具层（write_file / 终端 cwd / 沙箱 scratch）与产物仓都落这里，agent 写的文件树里才看得见。
N1：全部路由过 current_user（auth 开时按会话归属隔离，越权 404）。"""
import mimetypes
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request

from server.auth import current_user

router = APIRouter(prefix="/api/sessions", tags=["workspace"])


def _ws(request: Request, sid: str, user: str) -> Path:
    from server.api.sessions import _owned
    s = _owned(request, sid, user)
    root = Path(s.workspace)
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("/{sid}/workspace/files")
def files(sid: str, request: Request, user: str = Depends(current_user)):
    root = _ws(request, sid, user)

    def node(p: Path) -> dict:
        if p.is_dir():
            return {"name": p.name, "path": str(p), "type": "dir",
                    "children": [node(c) for c in sorted(p.iterdir())]}
        return {"name": p.name, "path": str(p), "type": "file", "size": p.stat().st_size}

    return {"exists": root.exists(), "tree": [node(c) for c in sorted(root.iterdir())]}


@router.get("/{sid}/workspace/file")
def file(sid: str, path: str, request: Request, user: str = Depends(current_user)):
    root = _ws(request, sid, user).resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root):        # startswith 会放行兄弟目录（"ws" 是 "ws_probe" 的前缀）
        raise HTTPException(400, "path out of workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    mime = mimetypes.guess_type(str(target))[0] or ("text/plain" if target.suffix in
                                                    {".md", ".py", ".txt", ".json"} else "application/octet-stream")
    # ext 是前端预览分发的判据（ToolsPanel.onSelect 按 md/image/.mmd 分支选渲染器）。
    # 此前响应没有这个字段，三类预览分支从未命中过——真浏览器第十五处（门禁 s8 t5 钉住）。
    ext = target.suffix.lower()
    if mime.startswith("image/"):
        return {"path": str(target), "ext": ext, "mime": mime}   # 二进制不回文本，前端走 /workspace 静态 URL
    return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace"),
            "mime": mime, "ext": ext}


@router.post("/{sid}/workspace/import_repo")
async def import_repo(sid: str, request: Request, user: str = Depends(current_user)):
    """把仓库导入成 SPO 图 + .mmd（批次2：ImportRepo 从死件接成有唯一调用者）。

    repo_path 取自 body，必须落在 workspace_root 内（防任意路径读取）；产物写进本会话工作区。
    """
    from codeharness.runtime import CURRENT_PROJECT, session_root
    body = await request.json()
    repo_path = Path(str(body.get("repo_path", ""))).resolve()
    ws_root = Path(session_root()).resolve().parent        # workspace_root（各会话目录的父）
    if not repo_path.is_dir():
        raise HTTPException(400, "repo_path 必须是已存在的目录")
    if not repo_path.is_relative_to(ws_root):
        raise HTTPException(400, "repo_path 必须在 workspace_root 内")
    # save_name 原样拼成 `{会话根}/{save_name}.json` 交给 load_from 读：`../` 可越界读任意 .json
    # （探针实测：../别的会话/repo 会把那个会话的图并进本次产物，N1 隔离在此失效）。
    # 判据与会话目录名同源（sessions.py `_single_dir_name`）。
    save_name = str(body.get("save_name", "repo")).strip()
    if not save_name or Path(save_name).name != save_name:
        raise HTTPException(400, "save_name 不能包含路径分隔")

    workspace = _ws(request, sid, user)                    # 顺带越权校验（404）
    tok = CURRENT_PROJECT.set(workspace.name)              # 让 ImportRepo 内 ArtifactStore 落到本会话
    try:
        from codeharness.actions.import_repo import ImportRepo
        return await ImportRepo(llm=None)._call({
            "repo_path": str(repo_path),
            "save_name": save_name,
            "include_files": bool(body.get("include_files", True)),
        })
    finally:
        CURRENT_PROJECT.reset(tok)
