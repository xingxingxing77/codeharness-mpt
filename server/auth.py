"""N1 账号边界（施工4 S8 新增的欠账）：用户存储 + token + FastAPI 依赖。

开关语义：`PLATFORM__AUTH` 默认 **关**——单机开发态现状不变（15 个门禁零破坏）；
置 1 时除 /api/health 与 /api/auth/* 外全部要求 Bearer token，session/记忆/配额按 user_id 隔离。

- 用户表 `server/data/users.json`：pbkdf2_hmac（每用户独立盐），首用户注册即建——单机平台无邀请制。
- token 进程内 dict + 7 天 TTL：重启重登，可接受。ponytail 上限：多 worker 部署时 token 不跨进程，
  nginx 轮询会 401——升级路径=token 存 redis（chat_factory 同款注入）。
- 隔离边界如实记：auth 只做**可见性**隔离（列表/读取/操作按 user 过滤），不做文件系统级隔离——
  workspace 目录仍按 project_name 平铺，同 project_name 跨用户在 create 时 409 拒绝（产物目录冲突）。
"""
import json
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.settings import SESSIONS_FILE

USERS_FILE = SESSIONS_FILE.parent / "users.json"
TOKEN_TTL = 7 * 24 * 3600
_USER_RE = re.compile(r"^[a-zA-Z0-9_-]{2,32}$")
# 读-改-写整文件必须串行：两笔并发 register 各自 load 到旧表、各自 save，后写的把先写的账号抹掉。
_USERS_LOCK = threading.Lock()


def _load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_users(users: dict):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def _hash(password: str, salt: str) -> str:
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()


def register(username: str, password: str) -> None:
    if not _USER_RE.match(username):
        raise HTTPException(422, "用户名只能是 2-32 位字母/数字/下划线/连字符")
    if len(password) < 6:
        raise HTTPException(422, "密码至少 6 位")
    salt = secrets.token_hex(16)
    hashed = _hash(password, salt)               # pbkdf2 120k 轮，放在锁外算，别把注册串行化
    with _USERS_LOCK:
        users = _load_users()
        if username in users:
            raise HTTPException(409, "用户名已存在")
        users[username] = {"salt": salt, "hash": hashed,
                           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_users(users)


def verify(username: str, password: str) -> bool:
    u = _load_users().get(username)
    return bool(u and secrets.compare_digest(u["hash"], _hash(password, u["salt"])))


# 登录失败计数（B12：原先 login 可无限试）。ceiling：进程内字典，多 worker 各算各的
# （与 token 同一坦白，升级路径=计数放 redis）；且按 username 记，拿到别人用户名的人
# 可以把那个账号短暂锁住——60s 窗口 + 5 次的代价换来「不再无限试」，划算。
_LOGIN_WINDOW = 60
_LOGIN_MAX_FAILS = 5
_login_fails: dict[str, list] = {}              # username -> [失败次数, 窗口起始]
_LOGIN_LOCK = threading.Lock()


def _login_locked_out(username: str) -> float:
    """返回还需等待的秒数，0 表示没被限。"""
    with _LOGIN_LOCK:
        ent = _login_fails.get(username)
        if not ent:
            return 0.0
        wait = _LOGIN_WINDOW - (time.time() - ent[1])
        if wait <= 0:
            _login_fails.pop(username, None)
            return 0.0
        return wait if ent[0] >= _LOGIN_MAX_FAILS else 0.0


def _login_failed(username: str):
    with _LOGIN_LOCK:
        ent = _login_fails.get(username)
        if ent and time.time() - ent[1] > _LOGIN_WINDOW:
            ent = None                           # 旧窗口作废，从这次重新计
        if ent:
            ent[0] += 1
        else:
            _login_fails[username] = [1, time.time()]
        if len(_login_fails) > 4096:             # 不存在的用户名也会进字典：到量清掉过期窗口
            now = time.time()
            for k in [k for k, v in _login_fails.items() if now - v[1] > _LOGIN_WINDOW]:
                _login_fails.pop(k, None)


def _login_ok(username: str):
    with _LOGIN_LOCK:
        _login_fails.pop(username, None)


class _Tokens:
    """进程内 token 表。ceiling 见模块 docstring。"""

    def __init__(self):
        self._t: dict[str, tuple[str, float]] = {}

    def issue(self, username: str) -> str:
        tok = secrets.token_urlsafe(32)
        self._t[tok] = (username, time.time() + TOKEN_TTL)
        return tok

    def resolve(self, token: str) -> str | None:
        ent = self._t.get(token)
        if not ent:
            return None
        user, exp = ent
        if time.time() > exp:
            self._t.pop(token, None)
            return None
        return user

    def revoke(self, token: str):
        self._t.pop(token, None)


tokens = _Tokens()


def auth_enabled() -> bool:
    from codeharness.configs.settings import settings
    return settings.platform.auth_enabled


def current_user(request: Request) -> str:
    """FastAPI 依赖：auth 关时恒 "default"（现状行为）；开时校验 Bearer token。
    隔离面用 404 不用 403——不向外部泄露"会话存在但不属于你"。
    ceiling：EventSource 发不了 header，/events 允许 `access_token` 查询参数等价
    （token 进 URL 有进日志的风险，仅 SSE 消费；换 cookie 是升级路径）。"""
    if not auth_enabled():
        return "default"
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else request.query_params.get("access_token", "")
    if not token:
        raise HTTPException(401, "未登录")
    user = tokens.resolve(token)
    if not user:
        raise HTTPException(401, "登录已过期")
    return user


class AuthReq(BaseModel):
    username: str
    password: str


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register_route(req: AuthReq):
    register(req.username, req.password)          # 注册失败当场 4xx
    return {"ok": True, "token": tokens.issue(req.username)}


@router.post("/login")
def login_route(req: AuthReq):
    wait = _login_locked_out(req.username)
    if wait:
        raise HTTPException(429, f"失败次数过多，请 {int(wait) + 1}s 后再试")
    if not verify(req.username, req.password):
        _login_failed(req.username)
        raise HTTPException(401, "用户名或密码错误")
    _login_ok(req.username)
    return {"ok": True, "token": tokens.issue(req.username)}


@router.post("/logout")
def logout_route(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        tokens.revoke(auth[7:].strip())
    return {"ok": True}


@router.get("/me")
def me_route(request: Request, user: str = Depends(current_user)):
    """票的真伪与归属：401 由依赖抛；auth 关返回 default。前端 init 用它验票。"""
    return {"user": user}
