"""S9.2 策略曲线（真钱通道，不进门禁）：同一 idea 跑 sop / role_zero / react 三档执行策略
（施工4 §9.2 第三件；N3 的 strategy 换装在装配面的组队表达）。

用法（一腿一次，产物逐腿追加）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe \
      tests/manual_strategy_curve.py --leg sop        # 经典 SOP 线
  ... --leg role_zero                                 # 动态 RoleZero 线
  ... --leg react                                     # 经典队形×REACT 循环

采集：status / 耗时 / token / 成本 / LLM 调用数（事件流里 on_chat_model_end 的 status 合流次数
替代——CostManager.records 在 runner 进程内，这里从事件流与 cost 快照取数）→
storage/benchmark/s9_strategy_curve.json。成本同表记录但不参与选型（§零：预算已作废）。"""
import argparse
import datetime
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import app

IDEA = ("实现一个命令行待办事项管理工具 todo.py：支持 add <任务>、list、done <编号>、rm <编号> "
        "四个子命令，数据保存在当前目录 todo.json；不用第三方依赖，带 --help 与用法示例；"
        "附 pytest 单元测试覆盖四个子命令。")

ROOT = Path(__file__).resolve().parent.parent
PARADIGM = {"sop": "classic", "role_zero": "dynamic", "react": "react"}
WINDOW = {"sop": 240, "role_zero": 160, "react": 240}     # 轮询窗（×5s）


def run_leg(client: TestClient, leg: str) -> dict:
    sid = client.post("/api/sessions", json={
        "idea": IDEA, "project_name": f"s9_curve_{leg}", "n_round": 6,
        "paradigm": PARADIGM[leg]}).json()["id"]
    print(f"[{leg}] session:", sid, "start:", client.post(f"/api/sessions/{sid}/start").status_code)
    t0 = time.time()
    s = {}
    for i in range(WINDOW[leg]):
        time.sleep(5)
        s = client.get(f"/api/sessions/{sid}").json()
        if s["status"] in ("finished", "failed", "stopped"):
            break
        if i % 24 == 23:
            print(f"  [{leg}] ...{s['status']} cost={s.get('cost')}")
    elapsed = round(time.time() - t0, 1)
    events = client.get(f"/api/sessions/{sid}/events/history?after=0").json()["events"]
    files = client.get(f"/api/sessions/{sid}/workspace/files").json()

    def walk(nodes, acc):
        for n in nodes or []:
            acc.append(n["name"])
            walk(n.get("children"), acc)
        return acc

    return {"leg": leg, "session": sid, "status": s.get("status"), "elapsed_s": elapsed,
            "cost": s.get("cost"), "error": str(s.get("error"))[:200],
            "events": len(events), "artifacts": walk(files.get("tree"), [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=list(PARADIGM), required=True)
    args = ap.parse_args()

    out = ROOT / "storage" / "benchmark" / "s9_strategy_curve.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        print("health:", {k: h[k] for k in ("ok", "llm_configured", "model")})
        row = run_leg(c, args.leg)
    row["created"] = datetime.datetime.now().isoformat(timespec="seconds")
    history = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    history.append(row)
    out.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"FINAL [{args.leg}]: {row['status']} | {row['elapsed_s']}s | "
          f"cost={json.dumps(row['cost'], ensure_ascii=False)} | events={row['events']} | "
          f"artifacts={row['artifacts']}；已追加 s9_strategy_curve.json（第 {len(history)} 条）")


if __name__ == "__main__":
    main()
