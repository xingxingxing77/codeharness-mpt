"""真模型端到端冒烟：走 server 的 HTTP 面跑一场真实会话。**花真钱，不进门禁**。

跑法（需 `.env` 已配真端点）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/manual_real_e2e.py

与九件套自测的分工：那九件一律 FakeLLM + 零外网（docs 全局规则 2「每步一个不花钱的门禁」），
本脚本负责它们照不出来的部分——真端点的 usage 字段形状、真实产出的形状漂移、runner 状态机。
看四件事：状态机走完、token 记账非 0、产物真落盘且前端文件树读得到、事件流可回放。

2026-09-15 首跑（qwen3.8-flash）现形并已修的四处：
  1. `cfg.stream` 默认 True → 用量只写 `usage_metadata`，旧 `add_usage` 读 `token_usage` → 记账恒 0；
  2. `structured()` 绕开 `ainvoke` 这个唯一记账出口 → 动态范式每轮思考不进账；
  3. `LLM__MAX_TOKENS`(复数) 绑不上 `max_token`(单数，照源)，thinking 模型 4096/8192 都截断，16384 才通；
  4. 产物文件名不设防：`filename=""` 会写到目录本身（PermissionError）；
     `task_list=[]` 生成零条 Send，会话以 `finished` 假成功收场、一行代码都不写。
"""
import json
import time

from fastapi.testclient import TestClient

from server.app import app

with TestClient(app) as c:
    h = c.get("/api/health").json()
    print("health:", {k: h[k] for k in ("ok", "llm_configured", "model")})
    sid = c.post("/api/sessions", json={
        "idea": "做一个命令行 tinycli：只有一个 main.py，提供 --version 打印 0.1.0。要求极简，不要测试文件。",
        "project_name": "real_e2e_e", "n_round": 2}).json()["id"]
    print("session:", sid, "start:", c.post(f"/api/sessions/{sid}/start").status_code)
    s = {}
    for i in range(150):
        time.sleep(4)
        s = c.get(f"/api/sessions/{sid}").json()
        if s["status"] in ("finished", "failed", "stopped"):
            break
        if i % 5 == 4:
            print(f"  ...{s['status']} round={s.get('n_round')} cost={s.get('cost')}")
    print("FINAL status:", s.get("status"), "| cost:", s.get("cost"), "| err:", str(s.get("error"))[:160])
    ev = c.get(f"/api/sessions/{sid}/events?after=0").json()
    events = ev.get("events") if isinstance(ev, dict) else ev
    kinds = {}
    for e in events or []:
        k = e.get("type") or e.get("block") or "?"
        kinds[k] = kinds.get(k, 0) + 1
    print("events:", len(events or []), kinds)
    files = c.get(f"/api/sessions/{sid}/workspace/files").json()
    print("tree:", json.dumps(files, ensure_ascii=False)[:300])
    src = c.get(f"/api/sessions/{sid}/workspace/file?path=main.py")
    print("main.py:", src.status_code, str(src.json())[:200] if src.status_code == 200 else src.text[:200])
