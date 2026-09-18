"""S9.3 平台定义性测试·UI 版（真钱通道，不进门禁）：不改任何内核文件，只经 ext_api 公共面
注册新角色 + 新 Action + 新 Tool + 新 SOP 模板 → 经真实 server 跑通完整会话 → 在 UI 上看到它。

与 s6 t19（进程内 FakeLLM 验收线）的差别：走**真 server 进程 + 真模型**，扩展件在
会话编排图（/graph）、Timeline 块（role=Poet 的 Docs 块）里可见——t19 证「装得上」，
本通道证「用得上、看得见」。

用法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe \
      tests/manual_ext_ui.py [--port 8718] [--keep]

--keep：会话结束后 server 继续挂着（前端 `npm run dev` 代理 8718，浏览器验收用），Ctrl+C 退出。
"""
import argparse
import asyncio
import json
import time

PORT = 8718
IDEA = "以李白的口吻，为命令行工具写一首四句的-promotional-打油诗，每句七个字。"


# ---- ① 扩展三件套 + 模板（唯一允许的扩展方式，全部经 ext_api 公共面） --------------------
def register_extension():
    from langchain_core.tools import tool as lc_tool

    from codeharness.base.action import BaseAction
    from codeharness.const import RequirementTag
    from codeharness.ext_api import register_action, register_role, register_template, register_tool
    from codeharness.roles.agent import Agent
    from codeharness.roles.registry import build_role
    from codeharness.schema import Message
    from codeharness.sop.templates import SopTemplate

    @register_action
    class Haiku(BaseAction):
        """扩展 Action：真模型作诗 + 经公共报道面（docs_block）上屏——UI 可见的部分。"""

        async def run(self, msg: Message) -> Message:
            from codeharness.report import docs_block
            verse = await self._aask(f"为这个主题写一首四句、每句七字的中文打油诗，只输出诗：\n{msg.content}")
            async with docs_block("haiku", role="Poet") as rep:
                await rep.content(verse)
            return Message(content=verse, role="assistant", cause_by=self.name, sent_from="Poet")

    @lc_tool
    def ext_count_chars(text: str) -> str:
        """统计文本字符数（扩展工具样例）"""
        return str(len(text))

    register_tool(ext_count_chars)
    register_role("Poet", lambda llm, **kw: Agent(
        {"name": "Poet", "profile": "Poet", "goal": "write haiku for the product"},
        [Haiku(llm=llm)], llm, watch={RequirementTag.USER_REQUIREMENT}))
    register_template(SopTemplate(
        name="ext_demo", desc="9.3 扩展线样例：需求直达 Poet，一首诗收场",
        assemble=lambda llm: {"Poet": build_role("Poet", llm)},
        edges={RequirementTag.USER_REQUIREMENT: ["Poet"]}))


# ---- ② 起 server（同进程：注册表对 app 生效） → 建会话(sop=ext_demo) → start → 等收口 ----
async def run_session(port: int) -> dict:
    import httpx

    from server.app import app
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
        h = (await c.get("/api/health")).json()
        print("health:", {k: h.get(k) for k in ("ok", "llm_configured", "model")})
        sid = (await c.post("/api/sessions", json={
            "idea": IDEA, "project_name": "ext_ui_demo", "n_round": 2,
            "sop": "ext_demo"})).json()["id"]
        print("session:", sid, "start:", (await c.post(f"/api/sessions/{sid}/start")).status_code)
        s = {}
        for i in range(60):                       # 单角色单调用，2 分钟窗足够
            await asyncio.sleep(2)
            s = (await c.get(f"/api/sessions/{sid}")).json()
            if s["status"] in ("finished", "failed", "stopped"):
                break
        print("FINAL:", s.get("status"), "| cost:", json.dumps(s.get("cost"), ensure_ascii=False),
              "| err:", str(s.get("error"))[:160])
        events = (await c.get(f"/api/sessions/{sid}/events/history?after=0")).json()["events"]
        kinds: dict = {}
        for e in events:
            key = f"{e.get('kind')}:{e.get('block') or ''}" + (f"@{e.get('role')}" if e.get("role") else "")
            kinds[key] = kinds.get(key, 0) + 1
        print("events:", len(events), json.dumps(kinds, ensure_ascii=False))
        print("graph:", (await c.get(f"/api/sessions/{sid}/graph")).json()["mermaid"])
        return {"sid": sid, "status": s.get("status"), "cost": s.get("cost"), "events": len(events)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keep", action="store_true", help="会话结束后 server 挂着供浏览器验收")
    args = ap.parse_args()

    register_extension()

    import uvicorn
    from server.app import app
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    serve = asyncio.create_task(server.serve())
    while not server.started:                     # 等端口就绪再发请求
        await asyncio.sleep(0.1)

    try:
        result = await run_session(args.port)
        ok = result["status"] == "finished" and result["events"] > 0
        print("ACCEPTANCE:", "PASS" if ok else "FAIL",
              f"（sop=ext_demo 经真 server 收口，{result['events']} 条事件）")
        if args.keep:
            print(f"server 挂在 http://127.0.0.1:{args.port} —— 前端 npm run dev 后浏览器验收，Ctrl+C 退出")
            await serve
    finally:
        if not server.should_exit:
            server.should_exit = True
        try:
            await asyncio.wait_for(serve, timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
