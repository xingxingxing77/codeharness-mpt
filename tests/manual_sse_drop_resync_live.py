"""C113 = C110 未验① 的端到端补完：**真丢包 → 断连 → 重连按游标补回，不重不漏**。

§1.8 已在活体服务上证过「溢出丢最旧」（15,000 条、队列钉 4096、丢 9,636），缺的是**补回那半**
（§1.7 未验① 的 a/c 两条边界）。本工装跑四臂，A/B **同形状、只差最后一步**——缺任何一臂，
另一臂都是假绿：

- A `hole`：一条真 SSE 连接消费到第 `PREFIX` 条后**一个字节不再读** ⇒ 真 TCP 背压把服务端该连接的
  订阅队列顶到 `MAX_SSE_QUEUE` ⇒ 丢最旧（现场断言 dropped>0）。不发重连 ⇒ 客户端账面只有 1..PREFIX，
  而 ring 里 `TOTAL` 条全在 ⇒ **洞是真数出来的**（没有这一臂，B 的「一条不缺」可能压根没丢过）。
- B `resync`：与 A 完全同形状，只是断开后按 `after=已应用游标` 重开一条 ⇒ 断言应用到的 seq 集合
  **逐字等于** `1..TOTAL`（判据是集合逐字相等，**不是条数相等**——条数相等挡不住错位/顶包）。
- C `overlap`：溢出**正在发生**时就断开重连，且发布端还在跑 ⇒ `/events` 那条「先 subscribe、
  再回放历史、再跟活流」的接缝上，历史与活流必然重叠。断言按产品同款游标规则应用后仍逐字覆盖
  1..TOTAL；重叠条数只报数、不判红（它是设计内的，靠去重挡）。
- D `window-edge`：**故意超出保留窗口**（发 `EDGE_TOTAL`，默认 16,001 > `MAX_EVENTS_PER_SESSION=15000`），
  把 §1.7 未验③ 那句「被挤出窗口那一截仍缺一截」从**语义推论**变成读数。断言三件：① ring 恰好留尾 15,000；
  ② 重连补回的那一段**连续且逐字等于窗口内段**（补回逻辑没错，只是没东西可补）；
  ③ 缺的那一截 = `被挤掉的 seq` 减去 `已应用前缀`，条数恰为 `(总条数-窗口)-已应用`。
  ⇒ 这条就是「补回深度的天花板是窗口不是队列」的实数，也是 C115（给 finished 会话卸 ring）的代价口径。

隔离实例：`PLATFORM__USE_REDIS=0`（进程内 bus）+ 会话表/workspace 指仓外 tmp + 端口 8824，
不连 Redis、不碰 db0/db15/8718/`.env`，**零模型调用（零花费）**。发布走 `app.state.bus.publish`——
就是产品 `_emit` 落到的那一个接缝，探针只是替 runner 按按钮。

用法：PYTHONIOENCODING=utf-8 F:/anaconda/python.exe -B tests/manual_sse_drop_resync_live.py
     可调：PREFIX=200 TOTAL=6000 OVER_TOTAL=9000 EDGE_TOTAL=16001 PORT=8824
     （A/B/C 三臂的发数必须 > MAX_SSE_QUEUE 且 <= MAX_EVENTS_PER_SESSION；D 臂**故意超窗口**，
      但必须超到「挤掉的头盖过已应用前缀」，否则量不出缺口——脚本开头两条断言各钉一边。）
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, "E:/Codeharness")
os.environ["PLATFORM__USE_REDIS"] = "0"
os.environ["LANGFUSE__ENABLED"] = "0"
os.environ["PLATFORM__AUTH_ENABLED"] = "0"

from pathlib import Path                            # noqa: E402

import server.settings as sset                      # noqa: E402
import server.sessions as smod                      # noqa: E402

STORE = Path("E:/tmp/streamx-c113/store")
STORE.mkdir(parents=True, exist_ok=True)
(STORE / "ws").mkdir(exist_ok=True)
sset.SESSIONS_FILE = STORE / "sessions.json"        # 会话表挪出仓库
sset.WORKSPACE_ROOT = STORE / "ws"
smod.SESSIONS_FILE = sset.SESSIONS_FILE
smod.WORKSPACE_ROOT = sset.WORKSPACE_ROOT

import uvicorn                                      # noqa: E402
from server.app import app                          # noqa: E402  create_app() 在 import 时执行
from server.events import (MAX_EVENTS_PER_SESSION, MAX_SSE_QUEUE,   # noqa: E402
                           cursor_of)

PORT = int(os.environ.get("PORT", "8824"))
PREFIX = int(os.environ.get("PREFIX", "200"))       # 失速前客户端真应用掉的条数（= 浏览器的 lastCursor）
TOTAL = int(os.environ.get("TOTAL", "6000"))        # > 队列上界 ⇒ 必溢出；<= ring 上界 ⇒ 全兜得住
OVER_TOTAL = int(os.environ.get("OVER_TOTAL", "9000"))   # overlap 臂多发+慢发，才保证重连时发布端还在跑
EDGE_TOTAL = int(os.environ.get("EDGE_TOTAL", "16001"))  # window-edge 臂：**故意超过真窗口 15000**

_opener = None


def http(method, path, body=None):
    """显式绕开系统代理：本机 Windows 代理会劫持 127.0.0.1（历史踩过隧道口 502 空体）。"""
    import urllib.request
    global _opener
    if _opener is None:
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=30) as r:
        return json.loads(r.read())


class Sse:
    """一条真 HTTP/SSE 连接：裸 socket，读不读由调用方决定（不读＝在服务端造真背压）。

    服务端是 `transfer-encoding: chunked`（uvicorn 对 StreamingResponse 的默认），所以这里必须
    真拆块：`raw` 里按 `长度\\r\\n<body>\\r\\n` 逐块取，拼进 `payload`，再按 SSE 的 `\\n\\n` 分帧。
    不拆块就会把 `e1\\r\\n` 这类块头当正文，一条也解析不出来（本轮第一发就是这么红的）。"""

    def __init__(self, sid, after):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=30)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.raw = b""                                # 线上新收到的字节（还带块头）
        self.payload = b""                            # 拆块之后的 SSE 流
        self.need = None                              # 当前块还差多少字节
        self.sock.sendall(("GET /api/sessions/%s/events?after=%s HTTP/1.1\r\n"
                           "Host: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"
                           % (sid, after)).encode())
        while b"\r\n\r\n" not in self.raw:            # 摘掉 HTTP 响应头
            self._fill(30.0)
        self.raw = self.raw.split(b"\r\n\r\n", 1)[1]

    def _fill(self, wait):
        self.sock.settimeout(wait)
        try:
            d = self.sock.recv(65536)
        except (socket.timeout, OSError):
            return False
        if not d:
            return False
        self.raw += d
        return True

    def _unchunk(self):
        """能拆多少拆多少；不够就等下一次 `_fill`。"""
        while True:
            if self.need is None:
                i = self.raw.find(b"\r\n")
                if i < 0:
                    return
                size = int(self.raw[:i].split(b";")[0], 16)
                self.raw = self.raw[i + 2:]
                if size == 0:                         # 终止块：流结束
                    self.need = -1
                    return
                self.need = size
            if self.need < 0:
                return
            if len(self.raw) < self.need + 2:         # 等块体 + 尾随 CRLF
                return
            self.payload += self.raw[:self.need]
            self.raw = self.raw[self.need + 2:]
            self.need = None

    def one(self, wait=2.0):
        """下一条事件（dict）；None＝这段时间里没有。`: keepalive` 注释行跳过、不算一条。"""
        end = time.time() + wait
        while True:
            self._unchunk()
            i = self.payload.find(b"\n\n")
            if i >= 0:
                frame, self.payload = self.payload[:i], self.payload[i + 2:]
                if frame.startswith(b"data: "):
                    return json.loads(frame[6:].decode())
                continue
            if time.time() >= end:
                return None
            self._fill(max(0.05, end - time.time()))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def publish_thread(sid, n, delay=0.02):
    """产品同款接缝：`bus.publish`（runner 的 `_emit` 落的正是这里）。"""

    def run():
        for i in range(1, n + 1):
            app.state.bus.publish(sid, kind="report", block="Thought", name="delta",
                                  value="%d|" % i + "补" * 12)
            if i % 100 == 0:
                time.sleep(delay)
    return threading.Thread(target=run, daemon=True)


def drain(conn, st, wait):
    """按产品同款规则消费（`frontend/src/stores/sessions.ts::ingest`（C188 之后去重从 `applyEvent` 挪进 `ingest`，规则一字未改）：`cursor <= lastCursor` 即丢）。

    就地更新 `st`（seqs / last_cursor / wire / dup），返回本次读数。"""
    wire = dup = 0
    while True:
        ev = conn.one(wait=wait)
        if not ev:
            return wire, dup
        wire += 1
        if st["last_cursor"] and ev["cursor"] <= st["last_cursor"]:
            dup += 1                                  # 历史与活流重叠那一段，游标规则挡下
            continue
        st["last_cursor"] = ev["cursor"]
        st["seqs"].add(ev["seq"])


def stall_until_dropped(sid):
    """失速期间盯该连接的队列与丢包计数，返回 (dropped, qsize)。"""
    q = app.state.bus._subscribers.get(sid, {})
    return (sum(app.state.bus._dropped.get(x, 0) for x in q),
            max((x.qsize() for x in q), default=0))


def main():
    assert MAX_SSE_QUEUE < MAX_EVENTS_PER_SESSION, "队列上界不小于 ring ⇒ 丢了补不回，前提破了"
    assert MAX_SSE_QUEUE < min(TOTAL, OVER_TOTAL) <= MAX_EVENTS_PER_SESSION, \
        "hole/resync/overlap 三臂的发数得落在「必溢出」且「ring 全兜得住」那一档"
    assert EDGE_TOTAL + 1 > MAX_EVENTS_PER_SESSION + PREFIX, \
        "window-edge 臂要**故意超出窗口**，且挤掉的那一截必须盖过已应用前缀，否则量不出缺口"
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    t0 = time.time()
    while not server.started:
        if time.time() - t0 > 60:
            raise SystemExit("服务 60 秒没起——不硬等，退出")
        time.sleep(0.05)
    print("隔离实例端口 %d，use_redis=0｜队列上界 %d，ring 上界 %d｜失速前消费 %d 条；"
          "hole/resync 发 %d 条，overlap 发 %d 条（慢发），window-edge 发 %d 条（**故意超窗口**）"
          % (PORT, MAX_SSE_QUEUE, MAX_EVENTS_PER_SESSION, PREFIX, TOTAL, OVER_TOTAL, EDGE_TOTAL))
    ok = True
    try:
        for arm, n in (("hole", TOTAL), ("resync", TOTAL), ("overlap", OVER_TOTAL),
                       ("window-edge", EDGE_TOTAL)):
            # seq=1 是 `POST /api/sessions` 自己发的那条 status（现证，不认它就对不齐总数）
            exp = set(range(1, n + 2))
            sid = http("POST", "/api/sessions",
                       {"idea": "C113 %s 臂，不跑模型" % arm, "paradigm": "react",
                        "permission": "readonly"})["id"]
            c = Sse(sid, "")
            st = {"seqs": set(), "last_cursor": ""}
            th = publish_thread(sid, n, delay=0.05 if arm == "overlap" else 0.02)
            th.start()                      # 先发起来才有流可消费（本工装第二发就是红在发序上）
            while len(st["seqs"]) < PREFIX:  # 边发边读，够 PREFIX 条就**一个字节不再读**
                ev = c.one(wait=30)
                if not ev:
                    raise SystemExit("%s 臂：只读到 %d 条就断流，没到 PREFIX=%d ⇒ 仪器没接上"
                                     % (arm, len(st["seqs"]), PREFIX))
                if ev["cursor"] > st["last_cursor"]:
                    st["last_cursor"] = ev["cursor"]
                    st["seqs"].add(ev["seq"])
            if arm == "overlap":            # 发布端还在跑，等到**真在丢**那一刻才断开重连
                for _ in range(300):
                    if stall_until_dropped(sid)[0] > 0:
                        break
                    time.sleep(0.1)
            else:
                while th.is_alive():
                    time.sleep(0.2)
                time.sleep(1.0)
            dropped, qmax = stall_until_dropped(sid)
            ring_n = len(http("GET", "/api/sessions/%s/events/history?limit=0" % sid)["events"])
            print("  [%s] 已消费 %d 条、游标停在 %s｜断开前 dropped=%d、qsize=%d（上界 %d）｜ring %d 条"
                  % (arm, len(st["seqs"]), st["last_cursor"], dropped, qmax, MAX_SSE_QUEUE, ring_n))
            if dropped <= 0:
                print("  [%s] ✗ 队列没溢出＝这根本没在验丢包，后面的判据恒真" % arm)
                ok = False
            # ring 那条硬账只能在**发布收完**之后核：overlap 臂此刻还在发（本轮现证 sampled 5401/9001），
            # 拿它当「底账缺不缺」会假红。window-edge 臂**故意超窗口** ⇒ 应到的是 min(总条数, 窗口)。
            exp_ring = min(n + 1, MAX_EVENTS_PER_SESSION)
            if arm != "overlap" and ring_n != exp_ring:
                print("  [%s] ✗ ring 条数 != 应有的 %d 条（总 %d 与窗口 %d 取小），底账不对（实 %d）"
                      % (arm, exp_ring, n + 1, MAX_EVENTS_PER_SESSION, ring_n))
                ok = False
            # 结构性判据：游标之后那一截客户端一条也没拿到——这才叫「洞真存在」。
            # 不写成「已消费恰好等于 1..PREFIX」：溢出可能在前缀内就发生，那条数值判据会假红。
            after_cur = {s for s in exp if cursor_of(s) > st["last_cursor"]}
            if st["seqs"] & after_cur:
                print("  [%s] ✗ 已消费集里混进了游标之后的 seq ⇒ 游标不是单调尾，前段读数不可信" % arm)
                ok = False
            still_live = th.is_alive()
            prefix = set(st["seqs"])              # 重连前客户端真应用掉的那一截（= 浏览器 lastCursor 之前）
            c.close()

            if arm == "hole":               # 不发重连：洞就该原样留在那儿
                print("  [hole] 不重连 ⇒ 缺 %d 条（首缺 seq=%s，末缺 seq=%s）"
                      % (len(after_cur), min(after_cur), max(after_cur)))
                if not after_cur:
                    print("  [hole] ✗ 连洞都没有，resync/overlap 两臂的绿就是恒真")
                    ok = False
                continue

            cur = Sse(sid, st["last_cursor"])
            w1, d1 = drain(cur, st, wait=6.0)
            th.join(timeout=120)
            if arm == "overlap":                 # 发布收完了，这才有资格核 ring 那条硬账
                ring_end = len(http("GET", "/api/sessions/%s/events/history?limit=0" % sid)["events"])
                print("  [overlap] 发布收尾后 ring %d/%d 条" % (ring_end, n + 1))
                if ring_end != n + 1:
                    print("  [overlap] ✗ 补回的底账缺条（实 %d）" % ring_end)
                    ok = False
            w2, d2 = drain(cur, st, wait=6.0)   # 发布端收尾后再把尾上读完
            cur.close()
            missing = sorted(exp - st["seqs"])
            extra = sorted(st["seqs"] - exp)
            if n + 1 > MAX_EVENTS_PER_SESSION:     # window-edge：窗口外那一截缺是**窗口口径**，不是补回逻辑的回归
                evicted = set(range(1, n + 2 - MAX_EVENTS_PER_SESSION))   # 被挤掉的头：seq 1..(总-窗口)
                expect = (exp - evicted) | prefix                        # 应到＝窗口内全段 + 已应用前缀
                off = sorted(st["seqs"] ^ expect)
                hole = sorted(evicted - prefix)
                tail_ok = sorted(st["seqs"] - prefix) == list(range(n + 2 - MAX_EVENTS_PER_SESSION, n + 2))
                want_hole = n + 1 - MAX_EVENTS_PER_SESSION - len(prefix)
                print("  [%s] ring 留尾 %d 条（上界 %d）｜补回后已消费集 %d 条｜窗口外永久缺 %d 条"
                      "（首缺 seq=%s 末缺 seq=%s；应等于 (总%d-窗口%d)-已应用%d=%d）"
                      % (arm, ring_n, MAX_EVENTS_PER_SESSION, len(st["seqs"]), len(hole),
                         hole[0] if hole else "-", hole[-1] if hole else "-",
                         n + 1, MAX_EVENTS_PER_SESSION, len(prefix), want_hole))
                print("  [%s] 补回段连续=%s｜已消费集与预期逐字相等=%s（差集 %d 条：%s）"
                      % (arm, tail_ok, not off, len(off), off[:5]))
                if off or not tail_ok:
                    print("  [%s] ✗ 补回的形状和窗口口径对不上——这不是「缺一截」，是补错了" % arm)
                    ok = False
                if len(hole) != want_hole:
                    print("  [%s] ✗ 缺口条数与 (总-窗口-已应用) 不等（实 %d 应 %d）" % (arm, len(hole), want_hole))
                    ok = False
                missing = extra = []
            print("  [%s] 重连线上收到 %d 条、游标规则挡下重叠 %d 条｜已消费集 %d 条｜缺 %d 越界 %d"
                  % (arm, w1 + w2, d1 + d2, len(st["seqs"]), len(missing), len(extra)))
            if missing or extra:
                print("  [%s] ✗ 补回不完整/越界：缺 %s 越界 %s" % (arm, missing[:5], extra[:5]))
                ok = False
            if arm == "overlap":
                if not still_live:
                    print("  [overlap] ✗ 重连时发布端已停 ⇒ 这臂退化成 resync 的重跑，没撞到接缝")
                    ok = False
                if (d1 + d2) == 0:
                    print("  [overlap] ! 这轮没撞上重叠（历史快照与活流没交叉）⇒ 重叠计数是**没样本**，不是没有")
    finally:
        server.should_exit = True
        time.sleep(1.0)
        import shutil
        shutil.rmtree("E:/tmp/streamx-c113", ignore_errors=True)
        print("收口：服务已令退出、tmp 目录已删（存在=%s）" % STORE.parent.exists())
    print("\nC113 端到端 %s" % ("✅ 四臂齐过：洞真存在 / 按游标补回逐字不重不漏 / 重叠竞态下仍完整 / "
                                "超窗口那截缺得可算（=总条数-窗口-已应用）"
                                if ok else "✗ 有臂不过——别记成闭合"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
