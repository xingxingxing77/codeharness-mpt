"""工作区文件树/读取。路径基准 = session.workspace = runtime.session_root()——
工具层（write_file / 终端 cwd / 沙箱 scratch）与产物仓都落这里，agent 写的文件树里才看得见。"""
import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/sessions", tags=["workspace"])


def _ws(request: Request, sid: str) -> Path:
    s = request.app.state.store.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    root = Path(s.workspace)
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("/{sid}/workspace/files")
def files(sid: str, request: Request):
    root = _ws(request, sid)

    def node(p: Path) -> dict:
        if p.is_dir():
            return {"name": p.name, "path": str(p), "type": "dir",
                    "children": [node(c) for c in sorted(p.iterdir())]}
        return {"name": p.name, "path": str(p), "type": "file", "size": p.stat().st_size}

    return {"exists": root.exists(), "tree": [node(c) for c in sorted(root.iterdir())]}


@router.get("/{sid}/workspace/file")
def file(sid: str, path: str, request: Request):
    root = _ws(request, sid).resolve()
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
