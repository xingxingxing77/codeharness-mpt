"""FastAPI 装配。MIME 修正块来自源 app.py:15-19（Windows 注册表 .js→text/plain 的坑，必须保留）。"""
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from server.settings import WORKSPACE_ROOT, load_llm_defaults
from server.events import SessionEventBus
from server.sessions import SessionStore
from server.bridges import LogBridge
from server.runner import SessionRunner
from server.api import sessions as sessions_api
from server.api import workspace as workspace_api
from server.api import approvals as approvals_api
from server.api import models as models_api


def _mask(v: str) -> str:
    return f"{v[:6]}...{v[-4:]}" if v and len(v) > 12 else ("***" if v else "")


def create_app() -> FastAPI:
    llm_defaults, llm_problem = load_llm_defaults()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        # S7 三接缝 feature flag 双跑（施工4 边界纪律）：默认进程内；PLATFORM__USE_REDIS
        # 且 ping 得通才换 Redis 实现，换不动就退回旧路并留话——**没有 Redis 也能起服务**。
        from codeharness.configs.settings import settings
        store = bus = None
        chat_factory = quota = None
        if settings.platform.use_redis:
            from platforms.session_store import RedisSessionStore
            from platforms.event_store import RedisEventBus
            st = RedisSessionStore()
            if st.ping():
                import server.sessions as _ss
                st.import_legacy(_ss.SESSIONS_FILE)     # 切默认一次性迁移：redis 空索引时搬 sessions.json
                                                        # （读 server.sessions 的模块属性而非 settings——自测把这份表指到 tmp，别把开发库搬进测试 db）
                st.heal_running()                       # 残态自愈（与进程内同语义），多 worker 各自启动时都跑一遍，幂等
                bus = RedisEventBus()
                bus.start()                             # sync→async 桥的 flusher（必须在运行中的 loop 里建）
                from platforms.chat_queue import RedisChatQueue
                from platforms.quota import Quota
                from platforms.trace import TraceStore
                store, chat_factory, quota = st, RedisChatQueue, Quota()
                runner_extra = (TraceStore(), True)
            else:
                from codeharness.logs import logger
                logger.warning("PLATFORM__USE_REDIS 已置位但 Redis 不可达——退回进程内实现")
        if store is None:
            bus = SessionEventBus()
            store = SessionStore()
            runner_extra = (None, False)
        log_bridge = LogBridge(bus)
        runner = SessionRunner(store, bus, llm_defaults or {}, chat_factory=chat_factory)
        runner.trace, redis_mode = runner_extra
        if redis_mode:
            runner.enable_redis_control()               # 跨 worker stop：PUBLISH ch:ctl + 本 worker 监听

        def _active():
            return [s.id for s in store.list()
                    if s.status.value in ("running", "awaiting_human")]

        log_bridge.install(_active)
        app.state.bus, app.state.store, app.state.runner = bus, store, runner
        app.state.quota = quota
        app.state.llm_defaults, app.state.llm_problem = llm_defaults, llm_problem
        yield
        # 停机成对拆：checkpoint.close_all 的 docstring 早就写了「server 应在 lifespan 关闭时
        # 调用它」（aiosqlite 的 worker 线程不显式关会留着），LogBridge 的 loguru sink 同理——
        # 不 remove 则 lifespan 每重启一次多挂一个 sink，往已死的旧 bus 里灌日志。
        from codeharness.environment.checkpoint import close_all
        from codeharness.observability import shutdown as tracing_shutdown
        log_bridge.remove()
        await close_all()
        tracing_shutdown()                          # N9：把队列里没发完的 span 冲干净再退
        if redis_mode:
            runner._ctl_task.cancel()                   # 先停监听再关连接（顺序反了就是 ConnectionError 栈）
            await bus.aclose()
            await runner._ctl.aclose()

    app = FastAPI(title="Codeharness Studio", lifespan=lifespan)

    # S1 守门（注册在 CORS 之前 = CORS 在外层，401/403 的响应头仍带 CORS）：
    # /workspace 是裸 StaticFiles 挂载、不经 current_user，而 WORKSPACE_ROOT 下除了各会话产物
    # 还住着 storage/checkpoints.db（完整黑板消息 + 角色记忆）。两条规矩：
    #   1) storage/ 一律 403——断点库从暴露面彻底摘掉，落盘位置不动、零迁移，auth 关也挡；
    #   2) auth 开时其余 /workspace/* 要求 access_token——与 current_user/SSE 同一条 ceiling 口径
    #      （<img> 与 EventSource 都发不了 header，token 只能进 query，代价见 B13 已知债）。
    # 判据走 resolve() 而不是字符串前缀：StaticFiles 也会把 .. / . / 反斜杠 / 大小写归一，
    # 两边必须按同一个落点说话，否则 `/workspace/./storage/x` 这类写法就从缝里漏出去。
    from urllib.parse import unquote
    from fastapi.responses import JSONResponse
    from server.auth import auth_enabled, tokens
    _WS_PREFIX, _STORAGE_ROOT = "/workspace", (WORKSPACE_ROOT / "storage").resolve()

    @app.middleware("http")
    async def guard_workspace(request, call_next):
        path = request.url.path
        if path != _WS_PREFIX and not path.startswith(_WS_PREFIX + "/"):
            return await call_next(request)
        rel = unquote(path[len(_WS_PREFIX):]).replace("\\", "/").strip("/")
        target = (WORKSPACE_ROOT / rel).resolve() if rel else WORKSPACE_ROOT.resolve()
        if target == _STORAGE_ROOT or target.is_relative_to(_STORAGE_ROOT):
            return JSONResponse(status_code=403, content={"detail": "storage 不对外"})
        if auth_enabled():
            auth = request.headers.get("Authorization", "")
            token = request.query_params.get("access_token", "") or (
                auth[7:].strip() if auth.startswith("Bearer ") else "")
            if not token or not tokens.resolve(token):
                return JSONResponse(status_code=401, content={"detail": "workspace 需要登录"})
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173",
                                                       "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(sessions_api.router)
    app.include_router(workspace_api.router)
    app.include_router(approvals_api.router)
    app.include_router(models_api.router)
    from server.auth import router as auth_router
    app.include_router(auth_router)

    @app.get("/api/health")
    def health():
        d = llm_defaults or {}
        from codeharness.configs.settings import settings
        return {"ok": True, "llm_configured": llm_defaults is not None,
                "llm_problem": llm_problem, "model": d.get("model", ""),
                "base_url": d.get("base_url", ""), "api_key_masked": _mask(str(d.get("api_key", ""))),
                "auth_enabled": settings.platform.auth_enabled,
                "workspace_root": str(WORKSPACE_ROOT)}

    app.mount("/workspace", StaticFiles(directory=str(WORKSPACE_ROOT)), name="workspace")
    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8718)
