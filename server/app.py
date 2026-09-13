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


def _mask(v: str) -> str:
    return f"{v[:6]}...{v[-4:]}" if v and len(v) > 12 else ("***" if v else "")


def create_app() -> FastAPI:
    llm_defaults, llm_problem = load_llm_defaults()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        bus = SessionEventBus()
        store = SessionStore()
        log_bridge = LogBridge(bus)
        runner = SessionRunner(store, bus, llm_defaults or {})

        def _active():
            return [s.id for s in store.list()
                    if s.status.value in ("running", "awaiting_human")]

        log_bridge.install(_active)
        app.state.bus, app.state.store, app.state.runner = bus, store, runner
        app.state.llm_defaults, app.state.llm_problem = llm_defaults, llm_problem
        yield

    app = FastAPI(title="Codeharness Studio", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173",
                                                       "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(sessions_api.router)
    app.include_router(workspace_api.router)

    @app.get("/api/health")
    def health():
        d = llm_defaults or {}
        return {"ok": True, "llm_configured": llm_defaults is not None,
                "llm_problem": llm_problem, "model": d.get("model", ""),
                "base_url": d.get("base_url", ""), "api_key_masked": _mask(str(d.get("api_key", ""))),
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
