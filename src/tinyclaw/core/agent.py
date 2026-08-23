"""Tinyclaw agent base: turns a handler coroutine into a full A2A server.

Subclasses implement ``handle(request, updater)`` and get, for free:

* the complete A2A task lifecycle (``submitted → working → … → completed``),
* distributed tracing with the incoming W3C tracecontext as parent,
* event reporting to the gateway observation plane,
* an Agent Card served at ``/.well-known/agent-card.json``.

The protocol's own ``input-required`` state is the human-in-the-loop pause
primitive: call ``updater.requires_input()`` and the task parks until a human
decision arrives as a new message on the same task.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    Message,
    Part,
    TextPart,
)

from .config import Settings
from .events import EventReporter
from .observability import tracing

log = logging.getLogger("tinyclaw.agent")


@dataclass
class AgentSpec:
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    skills: list[str] = field(default_factory=list)


@dataclass
class AgentRequest:
    """Normalized view over the incoming A2A message."""

    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    context_id: str = ""
    message: Message | None = None

    @property
    def is_resume(self) -> bool:
        """A follow-up message on an existing task (e.g. a human decision)."""
        return self.message is not None and self.message.task_id is not None


class TinyclawExecutor(AgentExecutor, ABC):
    def __init__(self, spec: AgentSpec, settings: Settings) -> None:
        self.spec = spec
        self.settings = settings
        self.events = EventReporter(settings)

    # -- A2A plumbing -------------------------------------------------------

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        request = AgentRequest(
            text="".join(p.root.text for p in (message.parts if message else []) if isinstance(p.root, TextPart)),
            data=next((p.root.data for p in (message.parts if message else []) if isinstance(p.root, DataPart)), {})
            or {},
            metadata=dict(getattr(message, "metadata", None) or {}),
            task_id=context.task_id or "",
            context_id=context.context_id or "",
            message=message,
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        parent = tracing.extract_trace_metadata(request.metadata)

        async with tracing.aspan(
            f"a2a.{self.spec.name}.execute", parent=parent, agent=self.spec.name, task_id=request.task_id
        ) as s:
            s.set_attribute("tinyclaw.resume", str(request.is_resume))
            try:
                await updater.submit()
                await updater.start_work()
                await self._state("working", request.task_id)
                await self.handle(request, updater)
            except Exception as exc:
                log.exception("agent %s failed on task %s", self.spec.name, request.task_id)
                await updater.failed(updater.new_agent_message(parts=[Part(root=TextPart(text=f"agent error: {exc}"))]))
                await self._state("failed", request.task_id, {"error": str(exc)})

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()
        await self._state("canceled", context.task_id)

    # -- subclass API ---------------------------------------------------------

    @abstractmethod
    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        """Do the work. Use updater.complete/reject/requires_input to finish."""

    async def artifact(self, updater: TaskUpdater, name: str, data: dict[str, Any], text: str = "") -> None:
        parts = [Part(root=DataPart(data=data))]
        if text:
            parts.append(Part(root=TextPart(text=text)))
        await updater.add_artifact(parts=parts, name=name)
        await self.events.report(
            "a2a.artifact", self.spec.name, {"artifact": name, "preview": text or str(data)[:200]}, task_id=None
        )

    async def say(self, updater: TaskUpdater, text: str, metadata: dict[str, Any] | None = None) -> None:
        await updater.new_agent_message(parts=[Part(root=TextPart(text=text))], metadata=metadata)

    async def _state(self, state: str, task_id: str | None, extra: dict[str, Any] | None = None) -> None:
        await self.events.report("task.state", self.spec.name, {"state": state, **(extra or {})}, task_id=task_id)


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------


def build_agent_card(spec: AgentSpec) -> AgentCard:
    skills = [
        AgentSkill(id=s.lower().replace(" ", "-"), name=s, description=f"{spec.name}: {s}", tags=["tinyclaw"])
        for s in (spec.skills or [spec.name])
    ]
    return AgentCard(
        name=spec.name,
        description=spec.description,
        url=spec.url,
        version=spec.version,
        default_input_modes=["text", "application/json"],
        default_output_modes=["text", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
    )


def build_agent_app(spec: AgentSpec, executor: TinyclawExecutor, task_store: Any = None) -> A2AStarletteApplication:
    """Assemble the A2A server. Pass a TaskStore for durable tasks (Phase 2):
    parked input-required tasks then survive an agent restart."""
    from a2a.server.tasks import TaskStore

    store = task_store if isinstance(task_store, TaskStore) else InMemoryTaskStore()
    handler = DefaultRequestHandler(agent_executor=executor, task_store=store)
    return A2AStarletteApplication(agent_card=build_agent_card(spec), http_handler=handler)


def serve(spec: AgentSpec, executor: TinyclawExecutor, host: str = "127.0.0.1") -> None:
    """Run one agent as a standalone uvicorn process.

    Tasks persist to data/tasks/<agent>.sqlite by default (durable approvals);
    disable with TINYCLAW_DURABLE_TASKS=0.
    """
    from .persistence import durable_task_store

    port = int(spec.url.rsplit(":", 1)[-1])
    app = build_agent_app(spec, executor, task_store=durable_task_store(spec.name)).build()
    tracing.setup_tracing(spec.name, executor.settings.otlp_endpoint)
    uvicorn.run(app, host=host, port=port, log_level="warning")
