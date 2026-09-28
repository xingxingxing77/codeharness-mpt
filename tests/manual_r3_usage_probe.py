"""R3 用量页那格的**活体工装**（手动跑，不进任何门禁；零花费、不碰 dev 资源）。

为什么要有这份文件：09-28 普查给召回链装了三笔计数（`recall_failures` / `recall_zero_hits` /
`recall_returned`）与写腿两笔，判据全在组件级（`tests/s5_memory_rag.py::t42/t44/t45`）与文本级
（`tests/s8_frontend_contract.py::t27`）。**「一场真会话跑完，GET 出口这三个键是否非零、那一格是否真在
浏览器里出现」这两件事此前没有任何读数**——而普查现证生产里这条腿 98% 是连不上的，
所以「失败数非零」才是它该长成的样子，这必须被看过一次才算数。

跑法（两个终端）：

    PYTHONPATH=$PWD:$PWD/logs REDIS__DB=15 LANGFUSE__ENABLED=0 \\
      python tests/manual_r3_usage_probe.py seed     # 本机桩当 LLM + embedding 指死端口
      python tests/manual_r3_usage_probe.py serve    # 起 127.0.0.1:8731，自挂 frontend/dist

然后浏览器开 `http://127.0.0.1:8731/` → 设置 → 用量。

**它为什么是零花费的**：LLM 指向本文件自带的 OpenAI 兼容桩（`_Stub`，回一份 `ZeroThought` JSON），
embedding 与 Qdrant 都指**死端口**（`PLAN.md` §6 第 6 条：不发云端请求的唯一合法姿势就是把那一档显式
指到死端口，让读数自己承认是失败形状）。所以这一场看到的 `recall_failures=2` 是**真路径写进真账本**的，
而 `returned/zero_hits` 为 0 也是真的（腿压根没通）。

三条硬约束（都是本仓铁律）：会话文件与工作区都改到 `workspace/_probe_r3/` 下 —— **不碰**
`server/data/sessions.json`、不碰 8718、不改 `.env`、`REDIS__DB=15`、`LANGFUSE__ENABLED=0`。
`paradigm` 必须是 `dynamic`：默认 classic 线在桩只回 `end` 的情况下会停在 `awaiting_human`，
那一场的账本全零（第一版就是这么读到 `pt=0` 的，不是数据路断了）。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "workspace" / "_probe_r3"
SFILE = PROBE / "sessions.json"
MODEL, PT, CT, PORT = "step-3.5-flash", 137, 29, 8731


def patch():
    """把实例的全部落点挪出 dev：会话文件、工作区、模型档、开关。"""
    from codeharness.configs.settings import settings
    settings.llm.model = MODEL
    settings.llm.stream = False
    settings.embedding.base_url = "http://127.0.0.1:1/v1"     # 死端口 ⇒ 召回必失败
    settings.embedding.api_key = "dead"
    settings.qdrant.url = "http://127.0.0.1:1"
    settings.enable_rag = True
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False
    import server.app as appmod
    import server.sessions as ss
    import server.settings as sset
    ss.SESSIONS_FILE = sset.SESSIONS_FILE = SFILE
    sset.WORKSPACE_ROOT = appmod.WORKSPACE_ROOT = PROBE
    settings.workspace_root = str(PROBE)
    return settings


class _Stub(BaseHTTPRequestHandler):
    """本机 OpenAI 兼容桩：回一份能让动态线收工的 ZeroThought。"""

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = {"id": "x", "object": "chat.completion", "created": 1, "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                    "content": json.dumps({"thought": "探针只看账本，先收口",
                                           "commands": [{"command_name": "end", "args": {}}]})}}],
                "usage": {"prompt_tokens": PT, "completion_tokens": CT, "total_tokens": PT + CT}}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def seed():
    """跑一场真装配，从 **GET 出口**读那三笔——这是本工装唯一的主张。"""
    settings = patch()
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.llm.base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    settings.llm.api_key = "stub-key"
    time = __import__("time")
    from fastapi.testclient import TestClient
    from server.app import create_app
    with TestClient(create_app()) as c:
        sid = c.post("/api/sessions", json={"idea": "只回一句你好，不要调工具",
                                            "project_name": "r3_probe",
                                            "paradigm": "dynamic"}).json()["id"]
        assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
        body = {}
        for _ in range(160):
            time.sleep(0.5)
            body = c.get(f"/api/sessions/{sid}").json()
            if str(body.get("status")) != "running":
                break
        lst = c.get("/api/sessions").json()
        rows = lst if isinstance(lst, list) else (lst.get("sessions") or [])
        mine = [r for r in rows if r.get("id") == sid]
        cost = (mine[0].get("cost") if mine else {}) or {}
        keys = {k: cost.get(k) for k in ("recall_failures", "recall_zero_hits", "recall_returned",
                                         "overflow_failed", "overflow_written")}
        print("status =", body.get("status"), " pt =", cost.get("total_prompt_tokens"))
        print("GET /api/sessions 三笔 =", keys)
        assert keys["recall_failures"], f"三笔全零 ⇒ 注入链或装配指认断了：{keys}"
        print("OK：会话级读数非零。接着 serve 看那一格；把 sessions.json 里的三笔清零重启即为阴性对照。")
    srv.shutdown()


def serve():
    patch()
    import uvicorn
    from server.app import create_app
    print(f"http://127.0.0.1:{PORT}/  →  设置 → 用量")
    uvicorn.run(create_app(), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    PROBE.mkdir(parents=True, exist_ok=True)
    (seed if sys.argv[1:] and sys.argv[1] == "seed" else serve)()
