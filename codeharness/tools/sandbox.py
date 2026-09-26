"""Python 代码沙箱：RunCode Action 的执行后端（源 actions/run_code.py → subprocess）。
共享件 `run_proc` / `kill_tree`：工具层与 Terminal 都用它们，别再各写一份超时收尸。"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from codeharness.runtime import session_root
from codeharness.schema import RunCodeContext, RunCodeResult
from codeharness.tools._boundary import safe_session_path

DEVNULL = asyncio.subprocess.DEVNULL


async def kill_tree(proc) -> None:
    """杀掉整个进程树。

    Windows 上 `proc.kill()` 只杀 cmd.exe 父进程，孙进程活着且攥住继承的管道 →
    随后 `communicate()` 永久不返回（实测：shell 里起的 python 卡死图节点）。
    ⚠ taskkill 的 pid 必须带 /PID 前缀，裸 pid 报「无效语法」而什么都没杀。
    """
    if proc.returncode is not None:
        return
    if sys.platform.startswith("win"):
        tk = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(proc.pid), stdout=DEVNULL, stderr=DEVNULL)
        await tk.wait()
    else:
        # ponytail: POSIX 未做 setpgid/killpg，孙进程会漏杀。升级路径：起进程时 start_new_session=True + os.killpg
        proc.kill()


async def _reap(proc, pumps) -> None:
    """取消路径的收尸：停 pump、杀进程树、等它真死。

    单独一段是为了能 `shield` 起来——调用方此刻**已经在取消中**，收尸不能再被同一个取消打断。
    收尸失败只留一声 warning：调用方随后会把原来那个 `CancelledError` 原样抛出去，别让收尸的
    异常把它盖掉（那会让上层分不清「子进程没收干净」与「这次是被取消的」）。"""
    for t in pumps:
        t.cancel()
    try:
        await kill_tree(proc)
        await asyncio.wait_for(proc.wait(), 5)
    except Exception as exc:
        from codeharness.logs import logger
        logger.warning(f"取消路径收尸没做干净（{type(exc).__name__}: {exc}）——子进程可能还在跑")


async def run_proc(argv, cwd: Path | None = None, timeout: int = 60, shell: bool = False,
                   env: dict | None = None) -> RunCodeResult:
    """跑子进程到结束或超时。超时杀进程树并留下超时前已产出的输出，return_code=-1。

    不用 `wait_for(proc.communicate())`：被取消的 communicate() 会把管道里已读到的数据丢掉
    （实测超时后 stdout 变空串），而超时输出恰恰是调用方最需要的信息。

    T6（09-26 全量审查留账）：**外部取消**这条原先没有收尸——下面两个 `except` 只管超时。
    会话 stop 走的是 `task.cancel()`（`server/runner.py` 取消跑图任务 → `roles/*._act` → 这里），
    `CancelledError` 会直接穿过这个函数：pytest/npm 这类子进程继续跑、且攥着继承去的管道，
    两条 pump 任务永远悬着（`terminal._kill` 与下面的超时支路都收了尸，只有这条路没有）。
    """
    kwargs = dict(cwd=str(cwd or session_root()),
                  # 子进程 python 对管道是块缓冲：不强制无缓冲，超时前 print 的东西全留在它自己的缓冲区里
                  env={**(env or os.environ), "PYTHONUNBUFFERED": "1"},
                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    # create_subprocess_exec 是 *args 签名：Windows 上传单个 list 不会被摊平，得自己摊
    proc = await (asyncio.create_subprocess_shell(argv, **kwargs) if shell
                  else asyncio.create_subprocess_exec(*argv, **kwargs))
    out, err = bytearray(), bytearray()

    async def pump(stream, sink):
        while chunk := await stream.read(4096):
            sink.extend(chunk)

    pumps = [asyncio.create_task(pump(proc.stdout, out)), asyncio.create_task(pump(proc.stderr, err))]
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await kill_tree(proc)
            await proc.wait()
            err.extend(f"[timeout after {timeout}s]".encode())
        try:
            await asyncio.wait_for(asyncio.gather(*pumps), 5)
        except asyncio.TimeoutError:
            for t in pumps:
                t.cancel()                       # ponytail: 孙进程攥住管道时到此为止，输出已尽力收全
    except asyncio.CancelledError:
        try:
            await asyncio.shield(_reap(proc, pumps))
        except asyncio.CancelledError:
            pass                                 # 二次取消：收尸尽力而为，但**必须**把取消原样抛出去
        raise
    return RunCodeResult(stdout=out.decode(errors="replace")[:20000],
                         stderr=err.decode(errors="replace")[:20000],
                         return_code=-1 if timed_out else (proc.returncode or 0))


async def run_python_code(code: str, timeout: int = 60) -> RunCodeResult:
    workdir = session_root() / "scratch"        # per-session：脚本与产物不跨会话互看
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / f"run_{uuid.uuid4().hex}.py"
    script.write_text(code, encoding="utf-8")
    return await run_proc([sys.executable, str(script)], cwd=workdir, timeout=timeout)


def _env_with_paths(ctx: RunCodeContext) -> dict | None:
    """源 run_code.py 语义：additional_python_paths 进 PYTHONPATH；没配则继承环境。

    F-B：这些路径同样出自 LLM 产出的结构化输出，原先只 `Path(p).resolve()` 就塞进去——
    会话外的目录能 shadow 标准库与第三方包（同 S3 的 `working_directory` 是执行面上
    挨着的两个字段，那轮只关了一个）。判据共用 `safe_session_path`，越界的一律丢弃：
    这里没有「退回会话根」这种合理替代（把 `/etc` 换成会话根等于凭空多条 import 路径），
    而丢掉后子进程 import 失败会照常从 stderr 回到角色眼前，是响的而不是静默降级。"""
    if not ctx.additional_python_paths:
        return None
    paths = [str(q) for p in ctx.additional_python_paths if (q := safe_session_path(p))]
    if not paths:
        return None
    return {**os.environ, "PYTHONPATH": os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}


async def run_context(ctx: RunCodeContext, timeout: int = 120) -> RunCodeResult:
    """RunCodeContext.command 形如 ["python", "-m", "pytest", "tests/"]

    working_directory 出自 LLM 产出的结构化输出，边界判据与文件工具共用 safe_session_path
    （S3：此前这里只 `Path(…).resolve()`，`..`/绝对路径既能在会话外执行，
    下面的 mkdir 还会把目录**建到会话外**）。越界退回会话根——界内执行，而不是拒绝整场。
    """
    workdir = safe_session_path(ctx.working_directory or "") or session_root()
    workdir.mkdir(parents=True, exist_ok=True)
    return await run_proc(ctx.command, cwd=workdir, timeout=timeout, env=_env_with_paths(ctx))
