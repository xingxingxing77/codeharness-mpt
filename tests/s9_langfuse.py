"""N9 外部可观测接缝门禁（本机自托管 Langfuse v4，探活式）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s9_langfuse.py
  LF_LIVE=1 同上                     # 追加 t3：真模型一发 + 工具/手工 span 回读断言（花真钱，默认跳过）

四组（t1/t4/t5 零成本）+ 探活：
- t1 零成本断言（永远跑）：关=全 no-op；开了但 key 缺=仍 no-op；开+双 key=handler/会话上下文/
  span 装饰器就位；runner 接线处 `config["callbacks"]` 真被塞上（唯一注入点）。
- **t4 有界停机（永远跑，不需要任何在线服务）**：C28——端点连不通时 `observability.shutdown()`
  必须在一个 grace 内回来；配「同批 span 直接调 SDK 的无界 shutdown 明显更久」的阳性对照，
  和「本地假端点真收到了导出」的反证（防「把可观测关掉当修慢」）。
- **t5 进程退出尾巴（永远跑，不需要任何在线服务）**：C29——C28 只把等待挪出了 lifespan，
  进程**整退**时那笔还在；判据打在**进程总墙钟**上，配「把反注册换成 no-op＝改前形状 ⇒ 钩子真跑、
  墙钟明显更久」的阳性对照，与「钩子退出时真跑了才落 marker」的归因读数（真凶是 OTel
  `TracerProvider.shutdown` 这个 atexit 钩子，不是 C28 猜的 `concurrent.futures` 非 daemon worker）。
- t2 探活（Langfuse 可达才跑）：Basic auth 回读通路（v2/observations），不建 span、不花钱。
- t3 可选活体（LF_LIVE=1）：真模型一发 + 一个本地工具调用 + 一个手工 span → flush → 回读该会话
  的三类 span：LLM generation / TOOL / 手工 retriever（真钱 ~一次 ping，默认跳过）。
门禁不挂在外部服务上：Langfuse 不通则 t2/t3 整段跳过并明说（s7 姿势）；t1/t4/t5 任何时候都必须跑到。
"""
import asyncio
import os
import sys
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


_RAW_SCRIPT = '''
import os, sys, time
sys.path.insert(0, os.environ["CH_ROOT"])
from codeharness import observability as obs
from codeharness.configs.settings import settings
settings.langfuse.enabled = True
settings.langfuse.public_key, settings.langfuse.secret_key = "pk-lf-t4raw", "sk-lf-t4raw"
settings.langfuse.host = os.environ["LF_HOST"]
settings.langfuse.shutdown_grace_sec = 2          # 本脚本不走它：调的就是 SDK 那一行（改前形状）
t0 = time.time()
cli = obs.client()
for i in range(40):
    with cli.start_as_current_observation(name=f"t4raw-{i}", as_type="span"):
        pass
cli.shutdown()
print(f"{time.time() - t0:.2f}", flush=True)
'''


def _raw_shutdown_seconds(host, cap=25):
    line = _run_script(_RAW_SCRIPT, cap, {"LF_HOST": host})
    try:
        return float(line)
    except (TypeError, ValueError):
        return None


_STUB_SCRIPT = '''
import os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.environ["CH_ROOT"])
got = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        got.append(raw)
        # 空 body + 200 才是 OTLP 的成功响应（ExportTraceServiceResponse 允许零字节）。
        # 回 JSON 会被导出端判失败并无限重试，而 flush 无界——09-24 实测把整份门禁挂住两次。
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
from codeharness import observability as obs
from codeharness.configs.settings import settings
settings.langfuse.enabled = True
settings.langfuse.public_key, settings.langfuse.secret_key = "pk-lf-t4live", "sk-lf-t4live"
settings.langfuse.host = f"http://127.0.0.1:{srv.server_address[1]}"
cli = obs.client()
with cli.start_as_current_observation(name="t4-marker-unique", as_type="span"):
    pass
box, done = {}, threading.Event()


def _f():
    tf = time.time()
    obs.flush()                       # flush 本性无界 ⇒ 拿线程等它，父进程还有 timeout 兜底
    box["t"] = time.time() - tf
    done.set()


threading.Thread(target=_f, daemon=True).start()
ok = done.wait(20)
print("FLUSH_OK" if ok else "FLUSH_TIMEOUT", f"{box.get('t', -1):.2f}",
      sum(1 for b in got if b"t4-marker-unique" in b), len(got), sep="|")
'''


def _run_script(src, cap, extra_env=None):
    """把一段探针脚本写到临时目录跑一发（**不落进仓库**），返回它 stdout 的末行；超时返回 None。

    为什么要子进程：C28 的两种形状（无界 shutdown、导出端点被进程钉死）都会**把等待留在本进程**，
    在门禁进程里跑就是拿整份 s9 去赌（09-24 实测挂过两次：一次 400 秒、一次 300 秒）。
    """
    import shutil
    import subprocess
    d = Path(tempfile.mkdtemp())
    try:
        script = d / "probe.py"
        script.write_text(src, encoding="utf-8")
        env = {**os.environ, "CH_ROOT": str(Path(__file__).resolve().parents[1]),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "REDIS__DB": "15",
               "LANGFUSE__ENABLED": "1", **(extra_env or {})}
        try:
            r = subprocess.run([sys.executable, "-B", str(script)], env=env, capture_output=True,
                               text=True, timeout=cap, errors="replace")
        except subprocess.TimeoutExpired:
            return None
        out = (r.stdout or "").strip()
        return out.splitlines()[-1] if out else None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _raw_shutdown_seconds(host, cap=25):
    """在子进程里跑改前那一行（无界 `client.shutdown()`），返回耗时；cap 内没跑完返回 None。

    为什么不在本进程跑：我第一版就是在门禁进程里直接调它，40 个 span 对着 refuse 的端口
    **400 秒没跑完**，把整份 s9 挂住了；而放弃它又会把尾巴留给本进程退出（C28 实测 ~7.6s/轮）。
    子进程既隔离了等待，也让「25 秒还没完」本身成为可读的判别结果。"""
    line = _run_script(_RAW_SCRIPT, cap, {"LF_HOST": host})
    try:
        return float(line)
    except (TypeError, ValueError):
        return None


def _live_export_verdict(cap=70):
    """子进程里起假 ingestion 端点，导出一条带 marker 的 span，返回 `[FLUSH_OK, 秒, 含marker数, 总请求数]`。"""
    line = _run_script(_STUB_SCRIPT, cap)
    if not line or "|" not in line:
        return None
    return line.split("|")


def t4_shutdown_is_bounded():
    """C28：可观测端点是死的，停机路径也必须**有界**。三格，全本地，不要求 Langfuse 在线。

      ① 有界：建客户端 → 40 个 span → 本仓 `observability.shutdown()`，`grace=2` 时单次进出墙钟
         必须 ≤ 4s；连做两次（顺带覆盖旧 t1 那条「关过客户端后同进程建不回来」的坑）。
      ② 阳性对照 = 本件的「它真的会等」：同一批 span 直接调 SDK 的 `client.shutdown()`（就是改前
         `observability.py` 里那一行），墙钟必须**明显大于** ①。少了这格，① 可以靠「根本没发 span」
         恒绿——那正是 C20 打过的假绿形状。
      ③ 不许拿「把可观测关掉」当修慢：起一个本地假 ingestion 端点（真 HTTP、真 SDK 发），
         断言桩里**真收到了**带这条 span 名字的请求，而停机墙钟仍在 grace 内。
    """
    from codeharness import observability as obs
    keep = settings.langfuse.model_copy()

    def cycle(n_spans=40, tag="t4"):
        """客户端 → 一串 span → 本仓 `shutdown()`，返回（墙钟, 是否按时冲完, warning 响了几声）。"""
        from codeharness.logs import logger as _lg
        t0 = time.time()
        cli = obs.client()
        for i in range(n_spans):
            with cli.start_as_current_observation(name=f"{tag}-span-{i}", as_type="span"):
                pass
        warned, orig = [], _lg.warning
        _lg.warning = lambda *a, **k: warned.append(a)
        try:
            on_time = obs.shutdown()
        finally:
            _lg.warning = orig
        return time.time() - t0, on_time, len(warned)

    settings.langfuse.enabled = True
    settings.langfuse.public_key, settings.langfuse.secret_key = "pk-lf-t4", "sk-lf-t4"
    settings.langfuse.shutdown_grace_sec = 2
    try:
        # ① 有界：真连不通的端口，两次进出都必须在一个 grace 多一点的时间内回来，
        #    且必须**自己承认没冲完**（on_time=False）并**恰好喊一声**——静默丢掉与刷屏都算坏
        settings.langfuse.host = os.environ.get("LF_DEAD_HOST", "http://127.0.0.1:1")
        b1, on1, w1 = cycle(tag="t4a")
        b2, on2, w2 = cycle(tag="t4b")
        assert b1 <= 4 and b2 <= 4, f"①失效：有界停机实测 {b1:.2f}s / {b2:.2f}s（上限 4s）"
        assert (on1, on2) == (False, False), f"①失效：端点不通却报告「按时冲完」（{on1}/{on2}）"
        assert (w1, w2) == (1, 1), f"①失效：降级喊话次数 {w1}/{w2}（要求恰好 1 声，静默与刷屏都算坏）"

        # ② 阳性对照 = 「它真的会等」。旧形状（直接 `client.shutdown()`）**不能在本进程里跑**：
        # 实测它 400 秒都没跑完（我第一版就这么把整份门禁挂住了），放弃还会把尾巴留给进程退出。
        # 所以放到子进程里，给它 CAP 秒——**超时就正好是无界的直接证据**。
        raw = _raw_shutdown_seconds(settings.langfuse.host, cap=25)
        assert raw is None or raw - b1 >= 1.0, \
            (f"②失效：无界的旧形状只比有界档多花 {None if raw is None else round(raw - b1, 2)}s"
             f"（旧 {raw} vs 有界 {b1:.2f}）——差值不到 1s 就说明这里量不到「它会等」，"
             "① 的绿不能算修掉了什么")

        # ③ 不许拿「关掉可观测」当修慢：本地假 ingestion 端点**真收到** SDK 的导出。
        # 必须放子进程：OTel 的 `TracerProvider` 是进程级的，langfuse 的 `_init_tracer_provider`
        # 看到默认 provider 已存在就不换 ⇒ **导出端点被这个进程的第一个客户端钉死**。①/② 已把它
        # 钉在死端口上，本进程里再改 `settings.langfuse.host` 是收不到的（09-24 实测：桩一个请求没收到）。
        # 这条不是测试技巧，是运维事实：**换 host 要重启进程**。
        verdict = _live_export_verdict(cap=70)
        assert verdict and verdict[0] == "FLUSH_OK" and int(verdict[2]) >= 1, \
            (f"③失效：子进程导出判定 {verdict!r}（格式 FLUSH_OK|flush秒|含marker请求数|总请求数）"
             "——收不到导出，那 ①/② 的快就只是「发不出去」，不构成反证")
        assert float(verdict[1]) <= 15, f"③失效：端点在线时 flush 也要 {verdict[1]}s ⇒ 慢不是端点造成的"
        _ok("t4", f"有界停机 {b1:.2f}s/{b2:.2f}s ≤4s、承认没冲完={on1 is False}、各喊一声={w1}/{w2}；"
                  f"无界旧形状子进程 25s 内跑完={raw is not None}"
                  f"（{f'{raw:.2f}s，比有界多 {raw - b1:.2f}s' if raw is not None else '没跑完'}）；"
                  f"在线真导出：{verdict[2]}/{verdict[3]} 个请求含该 span、flush {verdict[1]}s "
                  "⇒ 修的是等待，不是采集")
    finally:
        settings.langfuse = keep
        obs._client = obs._handler = None


# ---------------- t5 进程退出尾巴（C29，永远跑） ----------------
_EXIT_SCRIPT = '''
import atexit, os, sys, threading, time
sys.path.insert(0, os.environ["CH_ROOT"])
from codeharness import observability as obs
from codeharness.configs.settings import settings
settings.langfuse.enabled = True
settings.langfuse.public_key, settings.langfuse.secret_key = "pk-lf-t5", "sk-lf-t5"
settings.langfuse.host = os.environ["LF_HOST"]
settings.langfuse.shutdown_grace_sec = 2
# 归因仪器：provider 建起来**之前**包一层类方法 ⇒ atexit 钩子真在退出时跑了才落 marker。
# 不用 `atexit._ncallbacks()`：3.13 上它数的是槽位数，unregister 之后不降（C28 就被它骗过一次）。
try:
    from opentelemetry.sdk.trace import TracerProvider as _TP
    _orig_shutdown = _TP.shutdown

    def _logged(self):
        with open(os.environ["T5_MARK"], "a", encoding="utf-8") as fh:
            fh.write("otel-tracerprovider-atexit-ran\\n")
        return _orig_shutdown(self)

    _TP.shutdown = _logged
except Exception:
    pass
if os.environ["T5_KEEP_HOOK"] == "1":
    atexit.unregister = lambda *a, **k: None       # 改前形状：反注册不生效 ⇒ 钩子留在退出路径上
t0 = time.time()
cli = obs.client()
for i in range(40):
    with cli.start_as_current_observation(name="t5-%d" % i, as_type="span"):
        pass
t_b = time.time()
on_time = obs.shutdown()
t_a = time.time()
non_daemon = [th.name for th in threading.enumerate() if not th.daemon]
print("T5|%.2f|%.2f|%s|%s" % (t_b - t0, t_a - t_b, on_time, ",".join(non_daemon)), flush=True)
'''


def _run_exit_probe(keep_hook: bool, cap=60):
    """子进程走「建客户端 → 40 span → 本仓 `shutdown()`（grace=2，死端口）→ 退出」。

    **总墙钟由父进程量**（`subprocess.run` 前后）——判据就是「进程整退」，不是 lifespan 那一格。
    返回 dict：wall 总墙钟 / build 子进程自量的建客户端秒 / shut shutdown 秒 / on_time / non_daemon /
    hook_ran（退出时 OTel 那个 atexit 钩子真跑了没）。cap 内没跑出读数返回 None。"""
    import shutil
    import subprocess
    d = Path(tempfile.mkdtemp())
    try:
        script = d / "probe.py"
        script.write_text(_EXIT_SCRIPT, encoding="utf-8")
        mark = d / "mark.txt"
        env = {**os.environ, "CH_ROOT": str(Path(__file__).resolve().parents[1]),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "REDIS__DB": "15",
               "LANGFUSE__ENABLED": "1", "NO_PROXY": "127.0.0.1,localhost,::1",
               "LF_HOST": os.environ.get("LF_DEAD_HOST", "http://127.0.0.1:1"),
               "T5_KEEP_HOOK": "1" if keep_hook else "0", "T5_MARK": str(mark)}
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, "-B", str(script)], env=env, capture_output=True,
                               text=True, timeout=cap, errors="replace")
        except subprocess.TimeoutExpired:
            return None
        wall = time.time() - t0
        line = next((l for l in (r.stdout or "").splitlines() if l.startswith("T5|")), "")
        if not line:
            return None
        _, build, shut, on_time, non_daemon = line.split("|")
        return {"wall": wall, "build": float(build), "shut": float(shut),
                "on_time": on_time, "non_daemon": non_daemon, "hook_ran": mark.exists()}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t5_process_exit_tail():
    """C29：C28 只把等待挪出了 lifespan，**进程整退**时那笔还在（≈2.5s/轮）。四格，全本地零花费。

    归因是量出来的、不是猜的：真凶是 OTel `TracerProvider.shutdown` 这个 **atexit 钩子**
    （退出时又 join 一次卡在死端点上的导出 worker）。C28 猜的「解释器收尾 join `concurrent.futures`
    的非 daemon worker」被第 ④ 格否掉。

      ① 有界：本仓现状下「进程总墙钟 − 子进程自量的（建客户端 + grace）」≤ 1.5s（解释器启动 + 退出尾巴
         加起来有上界）。改前形状在这台机上这一项是 2.7s+。
      ② 阳性对照 = 「它真的会等」：把 `atexit.unregister` 换成 no-op（= 改前形状，钩子留在退出路径上），
         同一差值必须 ≥ ① + 1.0s。少了这格，① 可以靠「根本没有钩子」恒绿。
      ③ 归因闭合：改前形状那发 marker 必须存在、现状那发必须不存在——**钩子真跑了才算数**。
      ④ 否掉旧假设：两发退出前除主线程外不许有非 daemon 线程（出现即要重新归因）。

    端点在线时 span 照旧导得出去那一半由 t4③ 复用（同一条导出路径，本件一行没碰）。
    """
    fix = _run_exit_probe(keep_hook=False)
    keep = _run_exit_probe(keep_hook=True)
    assert fix and keep, f"子进程没跑出读数（现状={fix!r} / 改前={keep!r}）——先看是不是被 60s cap 打掉"
    out_fix = fix["wall"] - (fix["build"] + fix["shut"])
    out_keep = keep["wall"] - (keep["build"] + keep["shut"])
    # 四格**收集式**报红（不是 fail-fast）：变异工装要靠「哪几格红」判特异性，只看得到第一格是不够的
    fails = []
    if out_fix > 1.5:
        fails.append(f"①失效：进程整退在 grace 之外还花了 {out_fix:.2f}s（总墙钟 {fix['wall']:.2f}s − 建客户端 "
                     f"{fix['build']:.2f}s − grace {fix['shut']:.2f}s；上限 1.5s）")
    if out_keep - out_fix < 1.0:
        fails.append(f"②失效：改前形状只比现状多花 {out_keep - out_fix:.2f}s（{out_keep:.2f}s vs "
                     f"{out_fix:.2f}s）——差值不到 1s 就说明这里量不到「它会等」，① 的绿不能算修掉了什么")
    if not (keep["hook_ran"] and not fix["hook_ran"]):
        fails.append(f"③失效：钩子跑没跑与预期不符（改前 ran={keep['hook_ran']} / 现状 ran={fix['hook_ran']}）"
                     "——归因没闭合，别拿墙钟差当结论")
    if not (fix["non_daemon"] == "MainThread" and keep["non_daemon"] == "MainThread"):
        fails.append(f"④失效：退出前出现非 daemon 线程（现状 {fix['non_daemon']!r} / 改前 "
                     f"{keep['non_daemon']!r}）——C29 已实测尾巴不是它，出现即需重新归因")
    assert not fails, "t5 四格：" + "｜".join(fails)
    _ok("t5", f"进程整退：现状 {fix['wall']:.2f}s（grace 之外 {out_fix:.2f}s）/ 改前形状 "
              f"{keep['wall']:.2f}s（grace 之外 {out_keep:.2f}s）⇒ 尾巴 {out_keep - out_fix:.2f}s 归 OTel 的 "
              f"atexit 钩子（marker 只在改前形状出现={keep['hook_ran']}）；退出前非 daemon 线程只有主线程")


def main():
    global LF_UP
    asyncio.run(t1_gate_states())
    t4_shutdown_is_bounded()                  # C28：不依赖外部服务，必须先跑
    t5_process_exit_tail()                    # C29：同上，零网络零花费
    LF_UP = _langfuse_up()
    if not LF_UP:
        _skip("t2/t3", f"Langfuse 未起（{settings.langfuse.host}/api/public/health 不通）"
                       f"——起 E:\\langfuse 的 compose 后复跑")
        print("\ns9_langfuse: 3/3 过（t1 零成本 + t4 有界停机 + t5 退出尾巴），探活组待环境")
        return
    t2_api_readback()
    if os.getenv("LF_LIVE") != "1":
        _skip("t3", "需 LF_LIVE=1（发真模型调用，花真钱）")
        print("\ns9_langfuse: 4/4 过（t1 + t4 + t5 + t2）")
        return
    asyncio.run(t3_live_roundtrip())
    print("\ns9_langfuse: 5/5 全绿")


if __name__ == "__main__":
    main()