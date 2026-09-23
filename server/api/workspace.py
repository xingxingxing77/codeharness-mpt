"""工作区文件树/读取/知识库摄取。路径基准 = session.workspace = runtime.session_root()——
工具层（write_file / 终端 cwd / 沙箱 scratch）与产物仓都落这里，agent 写的文件树里才看得见。
N1：全部路由过 current_user（auth 开时按会话归属隔离，越权 404）。"""
import mimetypes
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from server.auth import current_user

router = APIRouter(prefix="/api/sessions", tags=["workspace"])

MAX_PREVIEW_BYTES = 5 * 1024 * 1024     # 文本预览上限：超了不读，直接 413（不另做下载通道）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024     # 单个上传文档上限（知识库摄取一次最多 20 个 × 20MB）
MAX_UPLOAD_FILES = 20


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
    size = target.stat().st_size
    if size > MAX_PREVIEW_BYTES:
        # 整份 read_text 会把一个几百 MB 的日志全量读进内存再序列化给浏览器（一次请求就是一次卡顿）；
        # 上限之上不读，直接 413。图片那条分支不在这里——它只回元数据，不读字节。
        raise HTTPException(413, f"文件 {size / 1048576:.1f}MB 超过预览上限 "
                                 f"{MAX_PREVIEW_BYTES // 1048576}MB，不在浏览器里预览")
    return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace"),
            "mime": mime, "ext": ext}


@router.post("/{sid}/workspace/import_repo")
async def import_repo(sid: str, request: Request, user: str = Depends(current_user)):
    """把仓库导入成 SPO 图 + .mmd（批次2：ImportRepo 从死件接成有唯一调用者）。

    repo_path 取自 body：auth 开=只许本会话工作区（跨会话导入会泄露别人的目录结构），
    auth 关=workspace_root 内即可（导入公共模板目录是合法用法）；产物一律写进本会话工作区。
    """
    from codeharness.runtime import CURRENT_PROJECT, session_root
    body = await request.json()
    workspace = _ws(request, sid, user)                    # 先定归属（越权 404），边界用它
    repo_path = Path(str(body.get("repo_path", ""))).resolve()
    if not repo_path.is_dir():
        raise HTTPException(400, "repo_path 必须是已存在的目录")
    from codeharness.configs.settings import settings
    if settings.platform.auth_enabled:
        # N1：auth 开 = 只许导入本会话目录。原先只判到 workspace_root，用户 A 可以把
        # repo_path 指到 B 的会话目录，把 B 的目录结构扫成自己的关系图。
        boundary, where = workspace.resolve(), "本会话工作区"
    else:
        boundary = Path(session_root()).resolve().parent   # auth 关：导入公共模板目录是合法用法
        where = "workspace_root"
    if not repo_path.is_relative_to(boundary):
        raise HTTPException(400, f"repo_path 必须在{where}内")
    # save_name 原样拼成 `{会话根}/{save_name}.json` 交给 load_from 读：`../` 可越界读任意 .json
    # （探针实测：../别的会话/repo 会把那个会话的图并进本次产物，N1 隔离在此失效）。
    # 判据与会话目录名同源（sessions.py `_single_dir_name`）。
    save_name = str(body.get("save_name", "repo")).strip()
    if not save_name or Path(save_name).name != save_name:
        raise HTTPException(400, "save_name 不能包含路径分隔")

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


def _unreachable(exc: BaseException) -> bool:
    """这条异常是不是「连不上某台服务」——顺着 cause/context 链找**连接级**失败。

    为什么不能按顶层类型判（两台各包一层，实测）：qdrant 客户端把 httpx 的 ConnectError 裹成
    `ResponseHandlingException("All connection attempts failed")`，openai 客户端裹成
    `APIConnectionError("Connection error.")`——两个类名都不提「连接」。链末端才是共同形状。
    ⚠ 只认连接族：不认 `OSError` 全部（磁盘写失败不该报「服务不可用」），也不并超时
    （「服务在但慢」是另一件要说清的事，今天仍走 500，别顺手合并口径）。"""
    import httpx
    node, depth = exc, 0
    while node is not None and depth < 8:                 # 有界遍历：库里再套几层也认，但不信无限链
        if isinstance(node, (httpx.ConnectError, ConnectionError)):
            return True
        node = node.__cause__ or node.__context__
        depth += 1
    return False


def _kb_down(written: list, errors: list, exc: BaseException) -> str:
    """运行环境故障要说清两件事：**没成功**，以及**东西还在不在**。

    C16 的原始症状正是这两句分家——端点先写盘、后摄取，向量库停着时界面只回一句
    `Internal Server Error`，用户以为文件没传上去，而原件其实好好躺在 `kb/` 里（且重传会覆盖，
    不丢数据，但没人告诉他这一点）。`.kbMsg` 是 `white-space: pre-line`，所以这里可以直接分行。"""
    names = "、".join(p.name for p in written[:5]) + ("…" if len(written) > 5 else "")
    lines = [f"向量服务连不上：{type(exc).__name__}: {str(exc)[:80]}",
             f"已写进 kb/ 的 {len(written)} 份原件没有切片：{names}",
             "起好服务后重传即可（检查 QDRANT__URL 与 EMBEDDING__BASE_URL），文件树里现在就能看见这些原件"]
    if errors:
        lines.append(f"另有 {len(errors)} 条在门口就被拒了（与向量库无关）：" + "；".join(errors[:3]))
    return "\n".join(lines)


@router.post("/{sid}/workspace/upload_kb")
async def upload_kb(sid: str, request: Request, files: list[UploadFile] = File(...),
                    user: str = Depends(current_user)):
    """把用户上传的文档灌进知识库切片（C3：`UploadKB` 的**唯一生产调用点**）。

    原件先落到 `<会话工作区>/kb/`（文件树看得见、取证留得住），摄取读的是**盘上那份文件**而不是
    内存里的字节——门禁与生产因此走同一条路，不会再出现「测的是构造参数」那种假象。
    门口判三件事，不进内核：文件名只取 `Path(name).name`（`../`、绝对路径在这里就被剥掉，
    落点再 resolve 复核一次做纵深）、后缀白名单、单文件与件数上限。
    部分成功是合法结局：`errors` 逐条给原因，`uploaded_count` 只算真写进去的点。
    """
    from codeharness.actions.upload_kb import door_refusal
    # 白名单住在摄取件里，不在这里另抄一份；C26：门口还要分得清「不收这种格式」与「这台机器读不了」
    workspace = _ws(request, sid, user).resolve()
    if not files:
        raise HTTPException(400, "至少要有一个文件")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"一次最多 {MAX_UPLOAD_FILES} 个文件，这次传了 {len(files)} 个")
    root = workspace / "kb"
    root.mkdir(parents=True, exist_ok=True)

    written, errors = [], []
    for f in files:
        name = Path(f.filename or "").name          # 只认basename：`../../etc/passwd` 塌成 `passwd`
        target = (root / name).resolve()
        if not name or name.startswith("."):
            errors.append(f"{f.filename!r}: 文件名为空或以点开头的隐藏文件不收")
            continue
        if not target.is_relative_to(root):         # basename 判据之外的第二道：真落点必须在 kb/ 里
            errors.append(f"{name}: 落点越出 kb/ 目录，已拒")
            continue
        if reason := door_refusal(target.suffix.lower()):
            errors.append(f"{name}: {reason}")        # 拒在门口：原件**不落盘**（C26——原先 .docx 先落进
            continue                                   # kb/ 再在摄取时抛 ModuleNotFoundError，用户以为进去了）
        data = await f.read()
        if not data:
            errors.append(f"{name}: 空文件")
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            errors.append(f"{name}: {len(data) / 1048576:.1f}MB 超过单文件上限 "
                          f"{MAX_UPLOAD_BYTES // 1048576}MB")
            continue
        target.write_bytes(data)
        written.append(target)

    from codeharness.runtime import CURRENT_PROJECT
    tok = CURRENT_PROJECT.set(workspace.name)       # 与 import_repo 同一接缝：切片按项目隔离
    try:
        from codeharness.actions.upload_kb import UploadKB
        try:
            result = await UploadKB(llm=None)._call({"files": [str(p) for p in written]}) if written else \
                {"uploaded_count": 0, "chunk_count": 0, "errors": []}
        except Exception as exc:
            if not _unreachable(exc):
                raise                   # 真 bug 一个字都不许吞：翻成「服务不可用」比 500 更糟
            raise HTTPException(503, _kb_down(written, errors, exc)) from exc
    finally:
        CURRENT_PROJECT.reset(tok)
    result["errors"] = errors + result["errors"]
    result["written"] = [p.name for p in written]
    return result
