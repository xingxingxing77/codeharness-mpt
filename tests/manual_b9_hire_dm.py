"""B9 未验①：**直聊点招进来的成员，说一句真话**（真模型、真装配、花真钱）。

判据原话（`plan/frontend.md` B9 行 未验边界①）：「招进来的成员没有真跑一场——`s23` 有真图门禁，
但那是替身模型；直聊下拉里点 Cleo 说话要等一次真装配，属 A4 那类要花钱的读数」。
本工装补的就是这一格：**真 `create_app()` + 真 `SessionRunner` + 真 `_make_llm` + 真 StepFun**，
招人走真路由（`POST /roles`）、直聊走真路由（`POST /chat` 带 `send_to`），答案从事件回放里取。

四格：
  ① Cleo 真被装配进这场（起跑后 `session.roles` 含 Cleo ⇒ 生效点「下一次起跑」这句话有读数）；
  ② Cleo 真答了话：事件流里有一条**来自 Cleo** 的非空 report（不是队长替她说话）；
  ③ 这一场真花到了钱：GET 出口的 `total_prompt_tokens/total_completion_tokens` 非零、
     `cost_cny` 照实打印（读的是产品那本账，不是估算）；
  ④ 收口干净：会话停住、临时 sessions 文件与 workspace 目录删掉、真端点出网数印出来。

闸（**发钱之前**判，撞闸 exit 3 印「跑到第几发」；抬闸要人拍，别改工装）：
  · `LLM_SEND_GATE_B9`（默认 16 发真 StepFun 请求）——一发 ≈¥0.005–0.015，封顶在两三成的量级；
  · 墙上时钟 `B9_CAP_SEC`（默认 300s）：Cleo 首答一出现就停，不陪一场多轮 SOP 跑完。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_b9_hire_dm.py

**这一份不算门禁**：挂在真端点与真凭据上，缺席直接 exit 1 说「没跑成」。只碰自己的 project 目录与
临时 sessions 文件，不碰 8718、不碰 db0、不碰 `.env`、不发任何 embedding（`enable_rag=False`）。
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEND_GATE = int(os.environ.get("LLM_SEND_GATE_B9", "16"))
CAP_SEC = float(os.environ.get("B9_CAP_SEC", "300"))
PROJECT = "b9hiredm"
_sent = 0


def _count():
    global _sent
    _sent += 1
    if _sent > SEND_GATE:
        print(f"🛑 撞闸：真端点已出网 {_sent} 发 / 闸 {SEND_GATE} 发——停手，抬闸要人拍")
        sys.exit(3)
    print(f"   [真端点] 第 {_sent}/{SEND_GATE} 发", flush=True)


def _install_send_gate():
    """在 openai SDK 最底层数真出网发数：langchain 也好、我们的网关也好，发出去几发就是几发。"""
    from openai.resources.chat.completions import AsyncCompletions, Completions

    orig_a, orig_s = AsyncCompletions.create, Completions.create

    async def gated_a(self, *a, **kw):
        _count()
        return await orig_a(self, *a, **kw)

    def gated_s(self, *a, **kw):
        _count()
        return orig_s(self, *a, **kw)

    AsyncCompletions.create, Completions.create = gated_a, gated_s


def main() -> int:
    import server.sessions as ss
    from codeharness.configs.settings import settings

    if not settings.llm.api_key:
        sys.exit("exit 1：.env 里没有 LLM 凭据，本工装不发空请求")
    if "stepfun" not in (settings.llm.base_url or ""):
        sys.exit(f"exit 1：LLM 端点不是 `.env` 那台（{settings.llm.base_url}）——"
                 "换成替身/本机模型跑出来的读数没有资格（PLAN §6 铁律 28）")
    _install_send_gate()

    import tempfile

    from fastapi.testclient import TestClient
    from server.app import create_app

    tmp = Path(tempfile.mkdtemp())
    keep = (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
            ss.SESSIONS_FILE)
    settings.enable_rag = False              # 本件问的是「她答没答」，不掺向量腿（也不发 embedding）
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False      # 进程内 store：不碰 db0，也不 heal 别人的在跑会话
    ss.SESSIONS_FILE = tmp / "sessions.json"
    ws = ROOT / "workspace" / PROJECT
    t0 = time.time()
    try:
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "问一句被招进来的人是谁",
                                                "project_name": PROJECT,
                                                "paradigm": "dynamic"}).json()["id"]
            r = c.post(f"/api/sessions/{sid}/roles",
                       json={"name": "Cleo", "profile": "数据分析师",
                             "goal": "只用一句话说清你是谁、你负责什么", "constraints": "不调工具",
                             "tools": []})
            assert r.status_code == 200, f"招人路由回 {r.status_code}: {r.text[:200]}"
            assert r.json()["takes_effect"] == "next_start", r.json()
            # ⚠ 回执里的 `roles` 此刻**必须还是空**：它由装配出口 `runner._prepare` 回填（s23 t4 钉的就是
            # 这条），所以「招到了」的证据在起跑之后（① 格），不在这里。第一版在这里 assert 被打回。

            assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
            deadline = t0 + CAP_SEC
            got, seen = None, 0
            while time.time() < deadline:
                s = c.get(f"/api/sessions/{sid}").json()
                if "Cleo" in (s.get("roles") or []) and s.get("status") != "created":
                    break
                time.sleep(1)
            roles = c.get(f"/api/sessions/{sid}").json().get("roles") or []
            assert "Cleo" in roles, f"①失效：起跑后 roles 里没有 Cleo（只有 {roles}）⇒ 招人没进装配"

            q = "Cleo，用一句话说清楚：你是谁、你负责什么？"
            r = c.post(f"/api/sessions/{sid}/chat", json={"content": q, "send_to": "Cleo"})
            assert r.status_code == 200, f"直聊投递回 {r.status_code}: {r.text[:200]}（会话须在 running）"

            def cleo_reports():
                """只认 **署名是她** 的 report（`role=="Cleo"`）且 value 是字符串。

                第一版写的是「事件的 JSON 里含 Cleo 字样」——那会命中队长**派任务给她**的那条
                `block="Task"` 事件（value 是 dict、她一个字没说），是个假绿。署名+字符串正文
                才是「她说了话」。
                """
                evs = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 0}).json()["events"]
                mine = [e for e in evs if e.get("kind") == "report" and e.get("role") == "Cleo"]
                out = [e for e in mine if isinstance(e.get("value"), str)
                       and len(str(e["value"]).strip()) >= 8]
                return len(evs), out, [(e.get("seq"), e.get("block"), e.get("name"),
                                        len(str(e.get("value") or ""))) for e in mine]

            seen, hits, shape = 0, [], []
            while time.time() < deadline:
                seen, hits, shape = cleo_reports()
                if hits:
                    got = hits[0]
                    break
                time.sleep(2)
            # 账本是**合流**进会话记录的（`session_store.py:116` 那句「cost 合流与 status 更新同刻」），
            # 所以 Cleo 答完那一刻 GET 里可能还是起跑时的零值——先等到它落地，再点停止。
            # （第一版在这里立刻读，读回 pt=0 把 ③ 判红：那是量早了，不是账本坏了。）
            cost = {}
            for _ in range(20):
                cost = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
                if cost.get("total_prompt_tokens"):
                    break
                time.sleep(2)
            c.post(f"/api/sessions/{sid}/stop")
            cost = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
            print(f"   署名为 Cleo 的 report 事件形状 (seq,block,name,长度)：{shape}")
            assert got, (f"②失效：{int(time.time() - t0)}s 内事件流里没有**署名 Cleo** 的非空字符串 report"
                         f"（共 {seen} 条事件、她的事件形状 {shape}、出网 {_sent} 发）——直聊点她，她没说话")
            print(f"   Cleo 的回报：{json.dumps(got, ensure_ascii=False)[:420]}")
            assert cost.get("total_prompt_tokens", 0) > 0 and cost.get("total_completion_tokens", 0) > 0, \
                f"③失效：这本账是零（{cost}）⇒ 这一场没真发到模型，读数的资格不成立"
            print(f"   产品账本：pt={cost['total_prompt_tokens']} ct={cost['total_completion_tokens']} "
                  f"cost_cny=¥{cost.get('cost_cny', 0):.5f}（闸＝{_sent}/{SEND_GATE} 发）")
            print("✅ B9① 真装配 + 真直聊读数：招进来的 Cleo 用真模型答了话"
                  f"（起跑后 roles 含她={roles}、{int(time.time() - t0)}s 收口）")
            return 0
    finally:
        (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
         ss.SESSIONS_FILE) = keep
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"   ④ 收口：workspace/{PROJECT} 与临时 sessions 已删={not ws.exists()}"
              f" · 真端点出网 {_sent} 发")


if __name__ == "__main__":
    sys.exit(main())
