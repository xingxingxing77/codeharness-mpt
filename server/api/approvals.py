"""工具审批端点（S11-批次36）。

两个口都很薄：**判定与挂起在内核的 gate 节点**（`codeharness/roles/{role_zero,agent}.py`），
这里只负责「看有什么待批」和「把回执写进台账 + 唤醒图」。台账在 `platforms/approval_store.py`，
写方是跑图的 worker、读方是 HTTP worker，所以跨进程也能对上。

唤醒复用现成的 `runner.answer_human`（同一个 `Command(resume=…)` 通道，不新造第二条恢复路径）；
gate 把 resume 值只当唤醒信号，结论一律回台账读——所以先答了队尾也不会张冠李戴。
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.auth import current_user

router = APIRouter(prefix="/api/sessions", tags=["approvals"])


class RespondReq(BaseModel):
    """值域照参考项目的 `ApprovalOutcome`：只有「允许一次」与「拒绝」，没有 always-allow。"""
    outcome: Literal["allowed-once", "rejected"]


@router.get("/{sid}/approvals")
def list_approvals(sid: str, request: Request, user: str = Depends(current_user)):
    from platforms.approval_store import ApprovalStore
    from server.api.sessions import _owned
    _owned(request, sid, user)
    st = ApprovalStore(sid)
    return {"pending": st.pending(), "decided": st.settled()}


@router.post("/{sid}/approvals/{aid}/respond")
async def respond(sid: str, aid: str, req: RespondReq, request: Request,
                  user: str = Depends(current_user)):
    from platforms.approval_store import ApprovalStore
    from server.api.sessions import _get, _owned
    _owned(request, sid, user)
    st = ApprovalStore(sid)
    if st.item(aid) is None:              # 台账里没这条：可能是伪造 id，也可能是被 TTL 清了
        raise HTTPException(404, f"approval {aid} not found")
    outcome = st.decide(aid, req.outcome)             # 首个回执生效，重复回执返回原结论
    _get(request, "bus").publish(sid, kind="approval", name="resolved",
                                 value={"id": aid, "outcome": outcome})
    return {"ok": _get(request, "runner").answer_human(sid, aid), "outcome": outcome}
