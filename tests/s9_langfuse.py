"""N9 外部可观测接缝门禁（本机自托管 Langfuse v4，探活式）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s9_langfuse.py
  LF_LIVE=1 同上                     # 追加 t3：真模型一发 + 工具/手工 span 回读断言（花真钱，默认跳过）

三组：
- t1 零成本断言（永远跑）：关=全 no-op；开了但 key 缺=仍 no-op；开+双 key=handler/会话上下文/
  span 装饰器就位；runner 接线处 `config["callbacks"]` 真被塞上（唯一注入点）。
- t2 探活（Langfuse 可达才跑）：Basic auth 回读通路（v2/observations），不建 span、不花钱。
- t3 可选活体（LF_LIVE=1）：真模型一发 + 一个本地工具调用 + 一个手工 span → flush → 回读该会话
  的三类 span：LLM generation / TOOL / 手工 retriever（真钱 ~一次 ping，默认跳过）。
门禁不挂在外部服务上：Langfuse 不通则 t2/t3 整段跳过并明说（s7 姿势）。
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

from codeharness.configs.settings import settings

LF_UP = False


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


def _skip(n, why):
    print(f"⏭ {n}: {why}")


class _Session:
    """最小会话替身：session_attributes 只读 id/user_id/paradigm/project_name 四个属性。"""

    def __init__(self, id="s9lf", paradigm="classic", project_name="s9lf"):
        self.id, self.paradigm, self.project_name = id, paradigm, project_name


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, sid, **kw):
        self.events.append((sid, kw))


def _langfuse_up() -> bool:
    """探活：health 无鉴权；401 也算「服务在」（有些版本要求鉴权）。"""
    import httpx
    try:
        r = httpx.get(f"{settings.langfuse.host}/api/public/health", timeout=3)
        return r.status_code < 500
    except Exception:
        return False


def _api(path: str, **params):
    import httpx
    c = settings.langfuse
    r = httpx.get(f"{c.host}{path}", params=params or None,
                  auth=(c.public_key, c.secret_key), timeout=10)
    return r


# ---------------- t1 零成本断言（永远跑） ----------------
async def t1_gate_states():
    from codeharness import observability as obs
    keep = settings.langfuse.model_copy()
    try:
        # ① 默认关：全 no-op（显式清掉 .env 里可能配好的真值，与部署状态解耦）
        settings.langfuse.enabled = False
        settings.langfuse.public_key = settings.langfuse.secret_key = ""
        assert obs.callbacks() == [], "关态必须返回空 callbacks"
        with obs.session_attributes(_Session(), "p"):
            pass                                        # nullcontext 可进

        @obs.span("t1.noop")
        async def _f(a):
            return a + 1
        assert await _f(1) == 2, "关态装饰器必须是原函数直通"

        # ② 开了但 key 缺：仍 no-op（不做半截链路）
        settings.langfuse.enabled = True
        assert not obs.enabled() and obs.callbacks() == [], "key 缺必须 no-op"

        # ③ 开 + 双 key：handler 与上下文就位
        settings.langfuse.public_key, settings.langfuse.secret_key = "pk-lf-t1", "sk-lf-t1"
        assert obs.enabled()
        cbs = obs.callbacks()
        assert len(cbs) == 1 and "CallbackHandler" in type(cbs[0]).__name__, cbs
        assert obs.callbacks()[0] is cbs[0], "handler 必须进程级单例（并发会话共用）"
        with obs.session_attributes(_Session(id="t1s", paradigm="dynamic"), "proj"):
            pass                                        # propagate_attributes 上下文可进

        # ④ runner 接线：_prepare 是唯一注入点，config["callbacks"] 必须被塞上
        import codeharness.team as team
        import server.sessions as ss
        from server.runner import SessionRunner
        keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"

        def fake_prepare(idea, project, agents=None, checkpointer=None, cost_manager=None, sop=None):
            return None, {"configurable": {"thread_id": project}}, {}

        async def _no_ck():
            return None

        saved = team.prepare_project
        team.prepare_project = fake_prepare
        try:
            r = SessionRunner(ss.SessionStore(), _Bus())
            r._saver = _no_ck                            # 不落 sqlite（接线断言不需要真断点）
            s = r.store.create("N9 接线", project_name="s9lf")
            r.projects[s.id] = "s9lf"                    # 生产路径由 _run/_ensure_graph 填
            _, cfg, _ = await r._prepare(s, "s9lf", None)
            assert len(cfg["callbacks"]) == 1, f"_prepare 未注入 handler: {cfg}"
            with r._session_ctx(s.id):
                pass                                     # 四个 ContextVar + 会话属性一起进出
        finally:
            team.prepare_project = saved
            ss.SESSIONS_FILE = keep_file
        _ok("t1", "开关三态 + handler 单例 + runner 两处接线（config.callbacks / _session_ctx）")
    finally:
        settings.langfuse = keep
        obs.shutdown()


# ---------------- t2 探活：回读通路（不花钱） ----------------
def t2_api_readback():
    """⚠ v1 的 /api/public/traces 在 v4 的 events_only 模式下 404——只能走 v2 observations。"""
    r = _api("/api/public/v2/observations", limit=5)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    data = r.json().get("data", [])
    print(f"   最近 observation {len(data)} 条：")
    for o in data[:5]:
        print(f"     - {o.get('type'):<10} {o.get('name')} | session={o.get('sessionId')} | "
              f"latency={o.get('latency')}s | {str(o.get('startTime'))[:19]}")
    _ok("t2", "Basic auth 回读通路可用（/api/public/v2/observations 200）")


# ---------------- t3 可选活体：一发真模型（LF_LIVE=1） ----------------
async def t3_live_roundtrip():
    from codeharness import observability as obs
    from codeharness.provider.gateway import LLMGateway
    from langchain_core.runnables.config import var_child_runnable_config

    sid = f"s9lf-live-{int(time.time())}"                   # 每次唯一：旧观察值不能冒充新落库

    @obs.span("probe.manual", as_type="retriever")      # 与记忆检索同一条手工 span 通路
    async def _manual_span_probe(q):
        return f"echo:{q}"

    from codeharness.tools.tool_registry import TOOL_REGISTRY
    read_file = TOOL_REGISTRY.select("read_file")[0]    # 本地工具，零成本：为一个 TOOL span

    tok = var_child_runnable_config.set({"callbacks": obs.callbacks()})   # 与图内继承同机制
    try:
        with obs.session_attributes(_Session(id=sid, paradigm="classic"), "s9lf_probe"):
            gw = LLMGateway()
            resp = await gw.ainvoke("ping")             # 走唯一出口，拿带 usage 的消息
            assert await _manual_span_probe("x") == "echo:x", "手工 span 装饰器没透传返回值"
            await read_file.ainvoke({"path": "n9_probe_missing.txt"})    # 工具调用靠继承成 span
        obs.flush()
    finally:
        var_child_runnable_config.reset(tok)

    usage = getattr(resp, "usage_metadata", None) or {}
    assert usage.get("input_tokens", 0) + usage.get("output_tokens", 0) > 0, \
        f"真模型没回 usage：{usage}（量化口径坏了，先修 gateway）"
    waited, got = 0, {}
    while waited < 30 and len(got) < 3:                  # 摄入是异步的（worker→clickhouse）
        await asyncio.sleep(3)
        waited += 3
        for o in _api("/api/public/v2/observations", limit=50).json().get("data", []):
            if o.get("sessionId") == sid:
                got[o.get("name")] = o
    assert "ChatOpenAI" in got, f"Langfuse 里找不到 session={sid} 的 LLM generation（等了 {waited}s）"
    assert "probe.manual" in got, f"手工 span 未落库：只见到 {list(got)}"
    assert "read_file" in got, f"工具 span 未落库：只见到 {list(got)}"
    assert got["ChatOpenAI"].get("latency"), got["ChatOpenAI"]
    assert got["read_file"].get("type") == "TOOL", got["read_file"]
    _ok("t3", f"三类 span 都已落 Langfuse（session={sid}）：LLM generation "
              f"latency={got['ChatOpenAI'].get('latency')}s、工具 {got['read_file'].get('type')} "
              f"read_file、手工 {got['probe.manual'].get('type')}（等 {waited}s）")


def main():
    global LF_UP
    asyncio.run(t1_gate_states())
    LF_UP = _langfuse_up()
    if not LF_UP:
        _skip("t2/t3", f"Langfuse 未起（{settings.langfuse.host}/api/public/health 不通）"
                       f"——起 E:\\langfuse 的 compose 后复跑")
        print("\ns9_langfuse: 1/1 过（t1 零成本），探活组待环境")
        return
    t2_api_readback()
    if os.getenv("LF_LIVE") != "1":
        _skip("t3", "需 LF_LIVE=1（发真模型调用，花真钱）")
        print("\ns9_langfuse: 2/2 过（t1+t2）")
        return
    asyncio.run(t3_live_roundtrip())
    print("\ns9_langfuse: 3/3 全绿")


if __name__ == "__main__":
    main()