"""工作区文件树/读取。路径基准 = session.workspace（与 ArtifactStore.active() 同目录）。"""
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
    if not str(target).startswith(str(root)):               # 路径越界防护
        raise HTTPException(400, "path out of workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    mime = mimetypes.guess_type(str(target))[0] or ("text/plain" if target.suffix in
                                                    {".md", ".py", ".txt", ".json"} else "application/octet-stream")
    return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace"),
            "mime": mime}
