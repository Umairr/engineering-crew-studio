"""A tiny thread-safe activity feed.

The flow writes milestones here and a CrewAI event listener adds a line every
time an agent starts work, delegates, or uses a tool. The Gradio UI polls it so
you can watch the team work live.
"""
from __future__ import annotations

import threading
from datetime import datetime

from crewai.events import (
    AgentExecutionStartedEvent,
    ToolUsageErrorEvent,
    ToolUsageStartedEvent,
    crewai_event_bus,
)


class ProgressLog:
    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        with self._lock:
            self._lines.append(line)
        print(f"\033[1;35m{line}\033[0m", flush=True)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def text(self, last: int = 400) -> str:
        with self._lock:
            return "\n".join(self._lines[-last:])


PROGRESS = ProgressLog()


def _short(value, limit: int = 90) -> str:
    text = str(value or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _role(value) -> str:
    return str(value or "Agent").strip()


def _tool_line(event: ToolUsageStartedEvent) -> str:
    who = _role(event.agent_role)
    args = event.tool_args if isinstance(event.tool_args, dict) else {}
    name = event.tool_name or "tool"
    lowered = name.lower()
    if "delegate" in lowered:
        return f"📨 {who} → delegates to {args.get('coworker', '?')}: {_short(args.get('task'))}"
    if "ask question" in lowered:
        return f"❓ {who} → asks {args.get('coworker', '?')}: {_short(args.get('question'))}"
    detail = args.get("filename") or ""
    return f"🔧 {who}: {name}{f'({detail})' if detail else ''}"


_registered = False


def register_listeners() -> None:
    global _registered
    if _registered:
        return
    _registered = True

    @crewai_event_bus.on(AgentExecutionStartedEvent)
    def _agent_started(source, event: AgentExecutionStartedEvent):  # noqa: ARG001
        task = getattr(event, "task", None)
        agent = getattr(event, "agent", None)
        role = _role(event.agent_role or getattr(agent, "role", None))
        desc = getattr(task, "description", "") or ""
        PROGRESS.log(f"🤖 {role} is working on: {_short(desc, 80)}")

    @crewai_event_bus.on(ToolUsageStartedEvent)
    def _tool_started(source, event: ToolUsageStartedEvent):  # noqa: ARG001
        PROGRESS.log(_tool_line(event))

    @crewai_event_bus.on(ToolUsageErrorEvent)
    def _tool_error(source, event: ToolUsageErrorEvent):  # noqa: ARG001
        PROGRESS.log(f"⚠️ {_role(event.agent_role)}: {event.tool_name} failed: {_short(getattr(event, 'error', ''))}")
