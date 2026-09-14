"""Python 代码沙箱：RunCode Action 的执行后端（源 actions/run_code.py → subprocess）。"""
import asyncio
import sys
import uuid
from pathlib import Path
from codeharness.configs.settings import settings
from codeharness.schema import RunCodeContext, RunCodeResult


async def run_python_code(code: str, timeout: int = 60) -> RunCodeResult:
    workdir = (Path(settings.workspace_root) / "scratch").resolve()   # resolve：cwd+相对路径会双重拼接
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / f"run_{uuid.uuid4().hex}.py"
    script.write_text(code, encoding="utf-8")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script), cwd=workdir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
        rc = proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        out, err, rc = b"", f"[timeout after {timeout}s]".encode(), -1
    return RunCodeResult(stdout=out.decode(errors="replace")[:20000],
                         stderr=err.decode(errors="replace")[:20000], return_code=rc)


async def run_context(ctx: RunCodeContext, timeout: int = 120) -> RunCodeResult:
    """RunCodeContext.command 形如 ["python", "-m", "pytest", "tests/"]"""
    proc = await asyncio.create_subprocess_exec(
        *ctx.command, cwd=str(Path(ctx.working_directory or settings.workspace_root).resolve()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
        rc = proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        out, err, rc = b"", f"[timeout after {timeout}s]".encode(), -1
    return RunCodeResult(stdout=out.decode(errors="replace")[:20000],
                         stderr=err.decode(errors="replace")[:20000], return_code=rc)
