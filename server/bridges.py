"""SESSION_ID + LogBridge（源 bridges.py 的 :10 与 :112-134；Reporter/Human 两桥已被报道槽/interrupt 取代）。
⚠️ 源 :29 教训生效：sink 回调必须是普通函数。"""
from contextvars import ContextVar
from typing import Optional

SESSION_ID: ContextVar[Optional[str]] = ContextVar("webui_session_id", default=None)


class LogBridge:
    def __init__(self, bus):
        self.bus = bus
        self._sink_id = None

    def install(self, active_sessions_provider, level: str = "INFO"):
        from codeharness.logs import logger

        def _sink(message):                       # 普通函数，不是绑定方法
            active = active_sessions_provider()
            if active:
                self.bus.broadcast_log(str(message).rstrip("\n"), active)

        self._sink_id = logger.add(_sink, level=level)

    def remove(self):
        if self._sink_id is not None:
            from codeharness.logs import logger
            logger.remove(self._sink_id)
            self._sink_id = None
