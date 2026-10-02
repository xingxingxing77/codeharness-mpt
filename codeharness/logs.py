#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2023/6/1 12:41
@Author  : alexanderwu
@File    : logs.py
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from contextvars import ContextVar
from datetime import datetime
from functools import partial
from typing import Any

from loguru import logger as _logger
from pydantic import BaseModel, Field

from pathlib import Path
METAGPT_ROOT = Path(__file__).parent.parent   # = E:\Codeharness

LLM_STREAM_QUEUE: ContextVar[asyncio.Queue] = ContextVar("llm-stream")

# C112（④，10-01 用户授权跨面修 codeharness/**）：LLM token 流队列的界。原先 `asyncio.Queue()` 无界，
# `log_llm_stream` 每片 `put_nowait`——消费端（Reporter._llm_stream_report → SSE）一旦比模型出片慢，
# 这条腿就在内存里无界堆正文。**不能照 SSE 那套丢最旧**：token 流没有可回放的真源，丢中间＝正文断字。
# 所以走**整段降级**：队列满 ⇒ 当段起后续逐片不再逐条入队，改攒进 `_spill`，段尾由 Reporter 合成
# **一条** content 事件补完 ⇒ 粒度退化（少掉打字机效果），正文一字不少。界取 4096（与 SSE 订阅队列同数）。
# ⚠ 可达性（18:0x 复查更正，别把这条当现网流式的护身符）：这座 `enable_llm_stream=True` 的桥**生产从没接**——
#   全仓只有 `tests/s3_report_action.py` 设真，块管理器走的是 `BlockReporter`→`_emit`（不经此队列），
#   现网打字机是 `runner._make_sink`/`_ProseStream` 发的 `live` 事件、其界在 SSE 订阅队列那一层（C90/C103）。
#   所以 C112 = 对休眠路径的廉价防御（有人重接这座桥时不无界），**不是修了现网内存病**；账见 platform-infra §1.11。
#   ⚠ C138 口径收窄（10-02 复审批）：「不无界」只对**队列对象**成立——满档后逐片改攒进 `_spill`
#   （普通 list，无上界），段内峰值内存与改前的无界队列同形，仍随正文长度走；补完腿的挂死面
#   （`await q.put` / `await task` 无超时）已在 report.py C138 上界（5s/10s 宁丢尾不挂死）。
MAX_LLM_STREAM_QUEUE = 4096


class ToolLogItem(BaseModel):
    type_: str = Field(alias="type", default="str", description="Data type of `value` field.")
    name: str
    value: Any


TOOL_LOG_END_MARKER = ToolLogItem(
    type="str", name="end_marker", value="\x18\x19\x1B\x18"
)  # A special log item to suggest the end of a stream log

_print_level = "INFO"


def define_log_level(print_level="INFO", logfile_level="DEBUG", name: str = None):
    """Adjust the log level to above level"""
    global _print_level
    _print_level = print_level

    current_date = datetime.now()
    formatted_date = current_date.strftime("%Y%m%d")
    log_name = f"{name}_{formatted_date}" if name else formatted_date  # name a log with prefix name

    _logger.remove()
    _logger.add(sys.stderr, level=print_level)
    # C106：文件那本必须有界（改前无 rotation/retention，实测单日可达 46.7 MB）。
    # ponytail: 界在「loguru 的 retention 只清**本 sink 同名族**的轮转副本」——09-30 实测
    # `retention=3` 留 3 份轮转 + 本体，而同目录里另一颗 sink 的 `day2.txt` 族一个都不动，
    # `retention='7 days'` 连 mtime 8 天前的别日子名也不删 ⇒ 按天命名之下这两参封的是
    # **单日本体的顶**，旧日文件仍随天数累积。要封目录总量得改成固定 sink 名，而那连带
    # `.gitignore`（本机现在只靠私有 `.git/info/exclude` 挡 `logs/*.txt`），不是一行能收的。
    _logger.add(METAGPT_ROOT / f"logs/{log_name}.txt", level=logfile_level,
                rotation="10 MB", retention=7)
    return _logger


logger = define_log_level()


def log_llm_stream(msg):
    """
    Logs a message to the LLM stream.

    Args:
        msg: The message to be logged.

    Notes:
        If the LLM_STREAM_QUEUE has not been set (e.g., if `create_llm_stream_queue` has not been called),
        the message will not be added to the LLM stream queue.
    """

    queue = get_llm_stream_queue()
    if queue:
        if getattr(queue, "_degraded", False):
            queue._spill.append(msg)          # 本段已整段降级：逐片先攒着，段尾一次补
        else:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                queue._degraded = True         # C112：满档即整段降级，此后不再逐条入队（保序）
                queue._spill.append(msg)
                logger.warning(
                    f"[llm-stream-degrade] 逐片队列满 {MAX_LLM_STREAM_QUEUE} 条，"
                    f"本段剩余片段改由段尾合成一条 content 上报（正文不丢，只退打字机粒度）")
    _llm_stream_log(msg)


def log_tool_output(output: ToolLogItem | list[ToolLogItem], tool_name: str = ""):
    """interface for logging tool output, can be set to log tool output in different ways to different places with set_tool_output_logfunc"""
    _tool_output_log(output=output, tool_name=tool_name)


async def log_tool_output_async(output: ToolLogItem | list[ToolLogItem], tool_name: str = ""):
    """async interface for logging tool output, used when output contains async object"""
    await _tool_output_log_async(output=output, tool_name=tool_name)


async def get_human_input(prompt: str = ""):
    """interface for getting human input, can be set to get input from different sources with set_human_input_func"""
    if inspect.iscoroutinefunction(_get_human_input):
        return await _get_human_input(prompt)
    else:
        return _get_human_input(prompt)


def set_llm_stream_logfunc(func):
    global _llm_stream_log
    _llm_stream_log = func


def set_tool_output_logfunc(func):
    global _tool_output_log
    _tool_output_log = func


async def set_tool_output_logfunc_async(func):
    # async version
    global _tool_output_log_async
    _tool_output_log_async = func


def set_human_input_func(func):
    global _get_human_input
    _get_human_input = func


_llm_stream_log = partial(print, end="")


_tool_output_log = (
    lambda *args, **kwargs: None
)  # a dummy function to avoid errors if set_tool_output_logfunc is not called


async def _tool_output_log_async(*args, **kwargs):
    # async version
    pass


def create_llm_stream_queue():
    """Creates a new LLM stream queue and sets it in the context variable.

    Returns:
        The newly created asyncio.Queue instance.

    C112：队列有界（`maxsize=MAX_LLM_STREAM_QUEUE`），并挂两个降级用的属性——`_degraded`（满后置真）、
    `_spill`（满之后攒的逐片，段尾由 Reporter 合成一条 content 补回）。缺这两个属性时 Reporter 侧按
    「没降级」处理，所以老代码路径零行为变化。
    """
    queue = asyncio.Queue(maxsize=MAX_LLM_STREAM_QUEUE)
    queue._degraded = False
    queue._spill = []
    LLM_STREAM_QUEUE.set(queue)
    return queue


def get_llm_stream_queue():
    """Retrieves the current LLM stream queue from the context variable.

    Returns:
        The asyncio.Queue instance if set, otherwise None.
    """
    return LLM_STREAM_QUEUE.get(None)


_get_human_input = input  # get human input from console by default


def _llm_stream_log(msg):
    if _print_level in ["INFO"]:
        print(msg, end="")