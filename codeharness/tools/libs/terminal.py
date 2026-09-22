"""Terminal：跨命令保态的常驻 shell（源 `tools/libs/terminal.py:17` 移植）。

与 `tools/__init__.py` 的 `execute_shell_async` 分工不同：那条一次一壳，`cd` 之后下一条命令就忘；
本件保态，SweAgent / DataInterpreter 这类"先定位再改再重跑"的循环要的是它。

五处适配（其余函数体照抄源）：
- `Config.default().workspace.path` / `DEFAULT_WORKSPACE_ROOT` → `runtime.session_root()`（按会话分配）
- `TerminalReporter()` → `report.terminal_block()`（同为 TERMINAL 块，前端 Terminal 面板不变）
- `@register_tool()` → 本仓注册表，tags=["terminal"]
- 新增 `timeout`：源是单人 CLI，这里跑在 FastAPI worker 里，一条挂死的命令会永久占住一个任务
- 源 `TERMINAL = Terminal()` 全进程一个壳 → 本仓按会话登记（`current_terminal()`），共用会把 B 会话带进 A 会话的 cwd

**不搬源 `Bash`(:185)**：它 `start()` 里 source 的 `SWE_SETUP_PATH` 脚本属于 `swe_agent_commands/`，
判定表 §四 已判 `弃`（SWE-bench 评测专用）。
"""
import asyncio
import os
import re
import sys
from asyncio import Queue
from asyncio.subprocess import PIPE, STDOUT
from typing import Optional

from codeharness.logs import logger
from langchain_core.tools import tool

from codeharness.report import END_MARKER_VALUE, terminal_block
from codeharness.runtime import CURRENT_PROJECT, session_root
from codeharness.tools.sandbox import kill_tree
from codeharness.tools.tool_registry import register_tool


class ShellDead(RuntimeError):
    """shell 在读到结束标记前就退出了——与挂死区分：调用方拿到一句说明，而不是永久等待。"""


class Terminal:
    """保态常驻 shell。`run_command` 之间保留 cwd 与环境变量，故同一条会话内可串命令。"""

    def __init__(self, timeout: int = 60):
        if sys.platform.startswith("win"):
            self.shell_command = ["cmd.exe"]
            self.executable = None
            self.command_terminator = "\r\n"
            self.pwd_command = "cd"
        else:
            self.shell_command = ["bash"]
            self.executable = "bash"
            self.command_terminator = "\n"
            self.pwd_command = "pwd"

        self.timeout = timeout
        self.stdout_queue = Queue(maxsize=1000)
        self.process: Optional[asyncio.subprocess.Process] = None
        # ponytail: cmd.exe 会把命令提示符一起回显进 stdout，输出里因此带 shell 提示行。
        # 源同样如此（照抄保真），要干净输出得上 PowerShell 或按提示符正则剥，等真被投诉再做。
        self.forbidden_commands = {
            "run dev": "Use Deployer.deploy_to_public instead.",
            "serve ": "Use Deployer.deploy_to_public instead.",
        }

    async def _start_process(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.shell_command,
            stdin=PIPE, stdout=PIPE, stderr=STDOUT,
            executable=self.executable,
            env=os.environ.copy(),
            cwd=str(session_root()),
        )
        await self._check_state()

    async def _check_state(self):
        """打印当前目录，确认 shell 活着（源语义）。"""
        output = await self.run_command(self.pwd_command)
        logger.info("The terminal is at:", output)

    async def run_command(self, cmd: str, daemon: bool = False, timeout: int | None = None) -> str:
        """执行命令并流式收输出，直到读到结束标记。daemon=True 时输出进 stdout_queue，返回空串。"""
        if self.process is None or self.process.returncode is not None:
            await self._start_process()

        output = ""
        commands = re.split(r"\s*&&\s*", cmd)
        skip_cmd = "echo Skipped" if sys.platform.startswith("win") else "true"
        for cmd_name, reason in self.forbidden_commands.items():
            for index, command in enumerate(commands):
                if cmd_name in command:
                    output += f"Failed to execute {command}. {reason}\n"
                    commands[index] = skip_cmd
        cmd = " && ".join(commands)

        self.process.stdin.write((cmd + self.command_terminator).encode())
        self.process.stdin.write(f"echo {END_MARKER_VALUE}".encode() + self.command_terminator.encode())
        await self.process.stdin.drain()

        limit = self.timeout if timeout is None else timeout
        try:
            if daemon:
                # 源 :103 漏传 daemon，stdout_queue 永远空、get_stdout_output 恒空串——移植时补上
                asyncio.create_task(self._read_and_process_output(cmd, daemon=True))
            else:
                output += await asyncio.wait_for(self._read_and_process_output(cmd), limit)
        except asyncio.TimeoutError:
            await self._kill("timeout")
            return output + f"[timeout after {limit}s] {cmd}"
        except ShellDead as e:
            await self._kill("exited")            # 丢掉死壳：returncode 未回收前不能被判还活着而重用
            return output + str(e)
        return output

    async def _kill(self, why: str):
        """命令挂死或 shell 退出：收尸并丢掉这个 shell，下次 run_command 自动重建。"""
        proc, self.process = self.process, None
        if not proc or proc.returncode is not None:
            return
        await kill_tree(proc)
        try:
            # 限时：超时路径上刚被取消的 read(1) 会让管道回收拖很久，进程树已死，不能阻塞调用方
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            logger.warning(f"terminal {why}: 子进程 5s 内未完成回收（进程树已杀，仅 transport 未关）")

    async def execute_in_conda_env(self, cmd: str, env: str, daemon: bool = False) -> str:
        """在指定 conda 环境里执行，Windows 用 activate &&，其余用 conda run -n。"""
        wrapped = f"conda activate {env} && {cmd}" if sys.platform.startswith("win") else f"conda run -n {env} {cmd}"
        return await self.run_command(wrapped, daemon=daemon)

    async def get_stdout_output(self) -> str:
        lines = []
        while not self.stdout_queue.empty():
            lines.append(await self.stdout_queue.get())
        return "\n".join(lines)

    async def _read_and_process_output(self, cmd: str, daemon: bool = False) -> str:
        async with terminal_block() as rep:
            cmd_output = []
            await rep.cmd(cmd + self.command_terminator)
            # 按字节读：文本模式会把 '\r' 转成 '\n'，导致重复输出（源注释，实测同样成立）
            tmp = b""
            while True:
                chunk = await self.process.stdout.read(1)
                if not chunk:
                    # 源在此 `continue`，进程一旦退出就变成空转死循环。web 进程里不能留这个洞。
                    raise ShellDead(f"[shell 已退出，未读到结束标记] {cmd}")
                *lines, tmp = (tmp + chunk).splitlines(True)
                for line in lines:
                    line = line.decode(errors="ignore")
                    ix = line.rfind(END_MARKER_VALUE)
                    if ix >= 0:
                        line = line[:ix]
                        if line:
                            await rep.output(line)
                            cmd_output.append(line)
                        return "".join(cmd_output)
                    await rep.output(line)
                    cmd_output.append(line)
                    if daemon:
                        await self.stdout_queue.put(line)

    async def close(self):
        """关掉常驻 shell。源用无限 `wait()`——命令还在跑就会永久卡住，这里限时并兜底按树收。"""
        proc, self.process = self.process, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 5)
        except Exception:   # 清理路径：任何异常都只保证一件事——不把壳留给调用方
            self.process = proc
            await self._kill("close")


_TERMINALS: dict[str, Terminal] = {}


def current_terminal() -> Terminal:
    """每会话一个常驻 shell。共用单个 shell 会让 B 会话在 A 的 cwd 里执行、并读到 A 的输出队列。"""
    key = CURRENT_PROJECT.get()
    if key not in _TERMINALS:
        _TERMINALS[key] = Terminal()
    return _TERMINALS[key]


async def close_terminal(project: str | None = None) -> None:
    """散会收壳——按会话登记的 shell 不主动关就是每会话漏一个常驻进程。默认关当前会话的。"""
    t = _TERMINALS.pop(project or CURRENT_PROJECT.get(), None)
    if t:
        await t.close()


@register_tool(tags=["terminal"])
@tool
async def terminal_command(command: str, conda_env: str = "") -> str:
    """在保态终端会话里执行命令：cd 与环境变量跨调用保留（要先切目录/进环境就用这条，而不是每次重敲）。
    conda_env 非空则在该 conda 环境内执行。
    关键词：终端、命令行、切目录、进环境、激活环境、常驻、看进程、看端口、服务起没起、top、netstat、run。
    示例：terminal_command(command="cd web && npm run build", conda_env="anaconda3")"""
    t = current_terminal()
    if conda_env:
        return await t.execute_in_conda_env(command, conda_env)
    return await t.run_command(command)
