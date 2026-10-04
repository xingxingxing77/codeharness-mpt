"""C172 的产线一档（零花费）：真 `create_app()` + 真 `ChatOpenAI`（打本机桩）+ 真 `POST /stop`，
数「在飞行中被停掉的那一笔，事件流里有没有人给它收口」。

为什么还要这一档：`s8_runner_meter` t16 打的是 `SessionRunner` 的翻译层与 `_forget`（单元级），
`frontend/scripts/shot.mjs` 那两帧打的是「closed 一位决定扫光与轮尾行」（渲染级）——中间那一格是
**产线这条路**：HTTP 起跑 → runner 建图 → 真 langchain 回调 → 用户点停止 → 事件流落定。
这一档不走通，「刷新也不会好」这句话就只有一半证据。

零花费与防呆（三件，缺一条就别跑）：
  · `settings.llm.base_url` 必须在覆盖后指向 127.0.0.1，否则这脚本会真出网烧钱 ⇒ 起跑前断言；
  · 桩自己数请求数，超过 N_STUB_CALLS 直接 500（一发变十发的形状在这仓烧过一次 ¥3.064）；
  · 墙钟 N_WALL_SEC 上限，撞顶就停并打印已看到的序列（不静默挂住）。
跑法：cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
      PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
      F:/anaconda/python.exe -B tests/manual_stop_stream_close.py
"""
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logs"))

N_STUB_CALLS = 6          # 本场最多让桩接几发（真出网不可能，这是防自己写错脚本）
N_WALL_SEC = 90
THOUGHT = ("这是一段足够长的思考正文，用来让逐片真的发布出来：先说清要做什么，"
           "再说为什么这么切，最后落到文件与验证步骤上，字数跨过二十四字的散文门槛。")


class Stub(BaseHTTPRequestHandler):
    calls = 0
    lock = threading.Lock()

    def do_POST(self):  # noqa: N802
        with Stub.lock:
            Stub.calls += 1
            n = Stub.calls
        if n > N_STUB_CALLS:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"gate: too many calls"}')
            return
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.dumps({"thought": THOUGHT, "commands": [{"command_name": "end", "args": {}}]},
                          ensure_ascii=False)
        frames = [json.dumps({"id": f"s{n}", "object": "chat.completion.chunk", "created": 1,
                              "model": "step-3.5-flash",
                              "choices": [{"index": 0, "delta": {"content": body[i:i + 9]},
                                           "finish_reason": None}]}, ensure_ascii=False)
                  for i in range(0, len(body), 9)]
        frames.append(json.dumps({"id": f"s{n}", "object": "chat.completion.chunk", "created": 1,
                                  "model": "step-3.5-flash",
                                  "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                               "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 500, "completion_tokens": 200,
                                            "total_tokens": 700}}))
        out = "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"
        b = out.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        for i in range(0, len(b), 384):
            seg = b[i:i + 384]
            try:
                self.wfile.write(f"{len(seg):X}\r\n".encode() + seg + b"\r\n")
                self.wfile.flush()
                time.sleep(0.06)          # 慢流：让「停止」真能落在飞行中
            except (OSError, socket.error):
                return                    # 客户端断了（被取消的那一发会走到这里）：安静退出
        try:
            self.wfile.write(b"0\r\n\r\n")
        except (OSError, socket.error):
            pass

    def log_message(self, *a):
        pass


def main() -> int:
    import tempfile
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/v1"

    from codeharness.configs.settings import settings
    settings.llm.base_url = base
    settings.llm.api_key = "stub-key"
    settings.llm.stream = True
    settings.enable_rag = False
    settings.platform.use_redis = False
    assert "127.0.0.1" in settings.llm.base_url, "base_url 没指到本机桩 ⇒ 会真出网，拒绝跑"

    import server.settings as se
    import server.sessions as ss
    tmp = Path(tempfile.mkdtemp())
    se.WORKSPACE_ROOT = tmp / "workspace"
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    ss.SESSIONS_FILE = tmp / "sessions.json"

    from fastapi.testclient import TestClient
    from server.app import create_app
    fails, out = [], {}
    t0 = time.time()
    with TestClient(create_app()) as c:
        sid = c.post("/api/sessions", json={"idea": "说一句就好，不要写文件", "project_name": "c176stop",
                                            "paradigm": "react", "permission": "readonly",
                                            "n_round": 1}).json()["id"]
        assert sid, "会话没建出来"
        r = c.post(f"/api/sessions/{sid}/start")
        assert r.status_code == 200, f"start 回执 {r.status_code} {r.text[:200]}"

        def hist():
            return c.get(f"/api/sessions/{sid}/events/history", params={"limit": 0}).json()["events"]

        # ① 前提自证：首片逐片真到过（不然测的是「压根没开始」而不是「飞行中被停」）
        saw_live = False
        while time.time() - t0 < 25:
            if any(e.get("name") == "live" for e in hist()):
                saw_live = True
                break
            time.sleep(0.1)
        out["saw_live_before_stop"] = saw_live
        if not saw_live:
            fails.append("① 前提没成立：停止之前一个逐片都没到，这格没测到「飞行中被停」")

        # ② 落刀：真 HTTP 的停止
        stop = c.post(f"/api/sessions/{sid}/stop")
        out["stop_http"] = stop.status_code
        mid = [(e.get("name"), e.get("uuid")) for e in hist() if e.get("kind") == "report"]
        out["reports_at_stop"] = mid[-6:]

        # ③ 等落态（stopped/finished/failed 都算收口完成）
        status = ""
        while time.time() - t0 < N_WALL_SEC:
            status = c.get(f"/api/sessions/{sid}").json().get("status", "")
            if status in ("stopped", "finished", "failed"):
                break
            time.sleep(0.2)
        out["final_status"] = status
        if status not in ("stopped", "finished", "failed"):
            fails.append(f"③ {N_WALL_SEC}s 内没落终态（现值 {status!r}）")

        evs = [(e.get("name"), e.get("uuid")) for e in hist() if e.get("kind") == "report"]
        # 产线不变量（形状无关）：散会之后**不许有任何还开着的块**——内核块靠 `finally: rep.close()`
        # 自己收（react/classic/dynamic 的每笔思考都包在 thought_block 里，本场就是这一形），
        # 块外调用的兜底行 `stream-*` 靠 `_forget` 补收（那一支由 s8_runner_meter t16 五格 + 变异 k1/k2/k5
        # 在翻译层钉着；本场的 react 线没走到块外调用，所以 stream-* 计数为 0 是**读数**不是漏测）。
        last_of: dict[str, str] = {}
        for n, u in evs:
            if u:
                last_of[u] = n
        dangling = {u: n for u, n in last_of.items() if n != "end_marker"}
        out["report_blocks"] = len(last_of)
        out["stream_rows"] = sorted(u for u in last_of if str(u).startswith("stream-"))
        out["dangling_blocks"] = dangling
        if not last_of:
            fails.append("② 事件流里一颗 report 块都没有 ⇒ 这格没在测收口（判红不判绿）")
        elif dangling:
            fails.append(f"② 产线停止后仍有没收口的块：{dangling}")

        cost = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
        out["stub_calls"] = Stub.calls
        out["ledger"] = {k: cost.get(k) for k in ("cost_cny", "total_prompt_tokens", "total_completion_tokens")}

    srv.shutdown()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    verdict = ("\n".join(f"❌ {f}" for f in fails)
               or f"✅ 产线一档过：真 /stop 落在飞行中 → {out['report_blocks']} 颗 report 块全部收口"
                  f"（其中兜底行 {len(out['stream_rows'])} 颗）、桩 {Stub.calls} 发、本机零出网")
    print(verdict)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
