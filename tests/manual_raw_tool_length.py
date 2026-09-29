"""零花费活体工装：**A 档（工具产出回喂 4 千）那一站，`clip` 之前收到的原始长度到底是多少**。

为什么落盘读路不够、还要再跑这一场（`tests/manual_truncation_lengths.py` 已经能按标记回收原长）：
09-29 现证现网回收不出来——那支探针按「整场晚于 R7 落仓」划队列，63 个 thread / 139 个 (thread, ns) 对里
A 档 47 条**全是 `{"name": "end", "result": "[结束]"}`**（长度 4），R7 之后一场都没真跑过工具；
R7 之前的 527 条又被旧 `[:4000]` censored（只剩 3 条恰=4000 当下界）。⇒ 原始分布只能**现跑一场、在 clip 之前埋计数**。

它同时回答第二件事，而且是本轮真正的发现：**两站叠套时「原长」这个词会骗人**。
`read_file` 自己先按 2 万切（`tools/__init__.py:41`），`role_zero` 再按 4 千切了回喂（`roles/role_zero.py:478`）。
读一个 3 万字的文件：第一站输出**正好 20000**（结尾写着「原长 30000 字」），第二站把这 20000 再切到 4000——
第一站那句标记正好落在被切掉的那截里，于是模型（以及事后从落盘回收原长的人）看见的「原长」是 **20000**，
比文件真大小少 1 万字；更糟的是 20001 字与 30000 字的文件在第二站**长得一模一样**。见 `nested_claim` 那格。

三条边界，别把这场读成别的：
  · **桩不是真模型**（`plan/PLAN.md` §4 第 4 条、§6 第 6 条）：`_Stub` 只负责「命令里写 read_file」。本文件量的是
    **工具产出的长度**与**截断链的形状**，与模型脾气无关；`SIZES` 是我设计的档位，**不是现网流量拟合**，
    所以「现网 A 档原始分布」这一格仍然开着（要它得有一场真跑过工具的现网会话，或给 `clip` 加常驻计数）。
  · 零花费：LLM 指本机桩，embedding 与 Qdrant 指**死端口**（不发云端请求的唯一合法姿势）。
  · 不碰 dev：会话文件、工作区与 **checkpoint 库**（`default_checkpoint_path(WORKSPACE_ROOT)`）全在
    `workspace/_probe_rawlen/` 下；`use_redis=False` + `REDIS__DB=15` + `LANGFUSE__ENABLED=0`，不碰 8718、`.env`、
    `server/data/sessions.json`、共享那份 `workspace/storage/checkpoints.db`；跑完连目录一起删并现证不存在。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_raw_tool_length.py > E:/tmp/rawlen_live.out 2>&1
"""
import json
import shutil
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
import manual_truncation_lengths as mtl      # noqa: E402  复用同一套落盘读路与同一把尺（raw_len/clip）

PROBE = REPO / "workspace" / "_probe_rawlen"
CKDB = PROBE / "storage" / "checkpoints.db"
PROJECT = "rawlen_probe"
MODEL = "step-3.5-flash"
A_BUDGET = 4000          # role_zero.py:478 工具结果回喂
READ_BUDGET = 20000      # tools/__init__.py:41 read_file
# 设计出来的尺寸档：恰在界下（阳性对照：不该有标记）/ 刚越过 4 千 / 越过 4 千一截 / 越过第一站的 2 万 / 远超两站
SIZES = (3999, 4001, 8000, 20001, 30000)

REC = []                 # 每笔 (站点, 预算, clip 之前收到的原始长度, 落盘长度)


def record(station):
    """把一站的 `clip` 换成「先记下原始长度、再原样调真件」。

    产品代码一个字不改，只在本进程里包一层；记的是 `len(text)`（`clip` 自己那行 `s = text if isinstance(...)`
    之前的高度），所以「非 str 入参被 str 化后变长」这种形状也一并记到。
    """
    def wrap(text, n):
        out = mtl.clip(text, n)
        REC.append((station, n, len(text if isinstance(text, str) else str(text)), len(out)))
        return out
    return wrap


def patch():
    from codeharness.configs.settings import settings
    import codeharness.roles.role_zero as rzmod
    import codeharness.tools as toolsmod
    settings.llm.model = MODEL
    settings.llm.stream = False
    settings.embedding.base_url = "http://127.0.0.1:1/v1"       # 死端口 ⇒ 不花钱、召回必失败
    settings.embedding.api_key = "dead"
    settings.qdrant.url = "http://127.0.0.1:1"
    settings.enable_rag = False
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False
    import server.app as appmod
    import server.sessions as ss
    import server.settings as sset
    ss.SESSIONS_FILE = sset.SESSIONS_FILE = PROBE / "sessions.json"
    sset.WORKSPACE_ROOT = appmod.WORKSPACE_ROOT = PROBE
    settings.workspace_root = str(PROBE)
    toolsmod.clip = record("read_file")                          # 第一站：2 万
    rzmod.clip = record("role_zero回喂")                          # 第二站：A 档 4 千
    return settings


class _Stub(BaseHTTPRequestHandler):
    """本机 OpenAI 兼容桩：第一发让剧本真去 read_file 读那五个文件，之后一律收工。"""

    served = False

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        if _Stub.served:
            thought, cmds = "文件都读过了，收口", [{"command_name": "end", "args": {}}]
        else:
            _Stub.served = True
            thought = "挨个读这几个大文件，看各回喂多少字"
            cmds = [{"command_name": "read_file", "args": {"path": f"big_{n}.txt"}} for n in SIZES]
        body = {"id": "x", "object": "chat.completion", "created": 1, "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": json.dumps({"thought": thought, "commands": cmds})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def read_back():
    """拿同一把尺（`mtl.raw_len`）去读**本探针自己**那份 checkpoint，把落盘值里的原长回收出来。

    这是 `raw_len` 在真落盘上的阳性对照：合成自检只证明我解析我拼的串，这一格证明它解析产品写下的串。
    """
    got = []
    with sqlite3.connect(f"file:{CKDB}?mode=ro", uri=True) as conn:
        saver = mtl.SqliteSaver(conn, serde=mtl._serde())
        for tid, ns in conn.execute("select distinct thread_id, checkpoint_ns from checkpoints"):
            tup = saver.get_tuple({"configurable": {"thread_id": tid, "checkpoint_ns": ns}})
            cv = (tup.checkpoint.get("channel_values") or {}) if tup else {}
            for step in (cv.get("history") or []):
                if not isinstance(step, dict):
                    continue
                for r in (step.get("results") or []):
                    if isinstance(r, dict) and isinstance(r.get("result"), str):
                        got.append((r.get("name"), r["result"], *mtl.raw_len(r["result"])))
    return got


def main() -> int:
    settings = patch()
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.llm.base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    settings.llm.api_key = "stub-key"
    root = PROBE / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    for n in SIZES:                       # 内容 = 同一个字重复 n 次 ⇒ 文件长度就是真值，没有别的加工
        (root / f"big_{n}.txt").write_text("字" * n, encoding="utf-8", newline="")
    from fastapi.testclient import TestClient
    from server.app import create_app
    t0 = time.time()
    with TestClient(create_app()) as c:
        sid = c.post("/api/sessions", json={"idea": "读这几个大文件并各说一句", "project_name": PROJECT,
                                            "paradigm": "dynamic", "permission": "readonly"}).json()["id"]
        assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
        status = None
        for _ in range(240):
            time.sleep(0.5)
            status = str(c.get(f"/api/sessions/{sid}").json().get("status"))
            if status != "running":
                break
    srv.shutdown()
    print(f"会话收工 status={status} 用时={round(time.time() - t0, 1)}s 桩第二发已用={_Stub.served}")

    print("\n站点读数（clip 之前收到的原始长度 / 落盘长度 / 被吃掉的字数）：")
    for station, n, raw, kept in REC:
        print(f"  {station:<14} 预算{n:>6}  原始{raw:>6}  落盘{kept:>6}  吃掉{raw - kept:>6}"
              f"  {'带标记' if kept < raw else '无标记（阳性对照：不该有）'}")
    a_over = [r for r in REC if r[0] == "role_zero回喂" and r[2] > A_BUDGET]
    r_over = [r for r in REC if r[0] == "read_file" and r[2] > READ_BUDGET]
    assert a_over, f"A 档那站没收到过超 {A_BUDGET} 的原始值 ⇒ 这场没 exercise 截断，读数作废"
    assert r_over, f"第一站没收到过超 {READ_BUDGET} 的原始值 ⇒ 叠套那一格没证据"

    back = read_back()
    marked = [b for b in back if b[3] == "marked"]
    print(f"\n落盘回收：A 值 {len(back)} 条（end 之类也算），其中带标记 {len(marked)} 条，"
          f"回收到的原长 = {sorted(b[2] for b in marked)}")
    assert marked, "落盘值一条都不带标记 ⇒ 要么没截断，要么读错了库"
    assert {b[2] for b in marked} <= {r[2] for r in REC}, "回收的原长里有埋计数没见过的大值 ⇒ 两把尺不一致"

    inner = next((r for r in r_over if r[2] == 30000), None)
    outer = next((r for r in a_over if r[2] == READ_BUDGET), None)
    assert inner and outer, "叠套那一格没拿到 30000→第一站→第二站 的成对读数"
    rec_30000 = [b[2] for b in marked if b[2] == READ_BUDGET]
    print(f"\nnested_claim：30000 字的文件 → 第一站落盘 {inner[3]}（原始 {inner[2]}）"
          f" → 第二站收到 {outer[2]}、切到 {outer[3]}；"
          f"落盘回收出来的「原长」= {sorted(set(rec_30000))} 与 20001 字那档**同值**"
          f"（第一站的标记躺在被切掉的那截里）⇒ 「原长」在叠套链上只承认真上一站的输出。")
    print("OK：A 档两站的原始长度、被吃掉的字数、以及叠套后「原长」失真都已有读数（桩驱动，零花费）。")
    if not cleanup():
        left = sorted(p.relative_to(PROBE).as_posix() for p in PROBE.rglob("*"))
        print(f"清场**没做成**：workspace/_probe_rawlen 还剩 {left}")
        return 1
    print("清场：workspace/_probe_rawlen 已删，现证不存在 = True")
    return 0


def cleanup():
    """删掉本探针的目录，并**证它真没了**。

    第一版在这里写了 `shutil.rmtree(..., ignore_errors=True)` + 一句 `not PROBE.exists()`，结果印出
    `False`：Windows 上 `checkpoints.db-shm/-wal` 会被最后一个 sqlite 连接多拖一小会儿（`s8` 加
    `_unlink_retry` 同因），`ignore_errors` 把失败咽了下去、话却照说。所以重试而不是咽。
    """
    import gc
    for _ in range(20):
        gc.collect()
        shutil.rmtree(PROBE, ignore_errors=True)
        if not PROBE.exists():
            return True
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    sys.exit(main())
