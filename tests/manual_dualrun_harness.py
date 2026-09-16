"""S9.1 双跑基线·本仓侧 runner（真钱冒烟，不进门禁）。
idea 字符串与 workspace/_s9_dualrun/source_classic_run.py 逐字同一份——双跑对照的前提。
采集：status / token 两径 / LLM 调用数（CostManager.records）/ 事件块统计 / 产物文件树。"""
import json
import time

from fastapi.testclient import TestClient

from server.app import app

IDEA = ("实现一个命令行工具 tinycli：接收一个目录路径参数，递归列出该目录下所有 .py 文件的路径；"
        "再实现统计每个文件行数的子命令；不用第三方依赖，带 --help 与用法示例；附单元测试。")

with TestClient(app) as c:
    h = c.get("/api/health").json()
    print("health:", {k: h[k] for k in ("ok", "llm_configured", "model")})
    sid = c.post("/api/sessions", json={
        "idea": IDEA, "project_name": "s9_dualrun3", "n_round": 5}).json()["id"]
    print("session:", sid, "start:", c.post(f"/api/sessions/{sid}/start").status_code)
    s = {}
    for i in range(420):   # 大 idea 多文件×BY_ORDER，~28min 窗口（首跑 10min 被切）
        time.sleep(4)
        s = c.get(f"/api/sessions/{sid}").json()
        if s["status"] in ("finished", "failed", "stopped"):
            break
        if i % 10 == 9:
            print(f"  ...{s['status']} cost={s.get('cost')}")
    print("FINAL:", s.get("status"), "| cost:", json.dumps(s.get("cost"), ensure_ascii=False),
          "| err:", str(s.get("error"))[:200])
    events = c.get(f"/api/sessions/{sid}/events/history?after=0").json()["events"]
    kinds: dict = {}
    for e in events:
        k = e.get("kind") or "?"
        b = e.get("block") or ""
        kinds[f"{k}:{b}" if b else k] = kinds.get(f"{k}:{b}" if b else k, 0) + 1
    print("events:", len(events), kinds)
    files = c.get(f"/api/sessions/{sid}/workspace/files").json()
    def walk(nodes, depth=0):
        for n in nodes or []:
            print("  " * (depth + 1) + n["name"], "size=", n.get("size"))
            walk(n.get("children"), depth + 1)
    print("tree:")
    walk(files.get("tree"))
