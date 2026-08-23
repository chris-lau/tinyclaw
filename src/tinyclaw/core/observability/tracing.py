"""OpenTelemetry wiring for every tinyclaw service.

Design points:

* One tracer per process; spans export over OTLP **only** when
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (e.g. self-hosted Langfuse), so the
  platform runs fine with zero observability infrastructure.
* W3C tracecontext is propagated **inside A2A message metadata** so one
  distributed trace follows a request across every agent hop — check the
  ``traceparent`` key on any message in the UI/task timeline.
* LLM calls are recorded with GenAI semantic-convention attributes so the
  backend (Langfuse) can render prompt/completion/token detail.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

log = logging.getLogger("tinyclaw.tracing")

TRACER: trace.Tracer = trace.get_tracer("tinyclaw")

_provider_set = False


def setup_tracing(service_name: str, otlp_endpoint: str | None) -> None:
    """Initialize the process-wide TracerProvider. Safe to call once at startup."""
    global TRACER, _provider_set
    if _provider_set:
        return
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
            log.info("OTLP span export enabled -> %s", otlp_endpoint)
        except Exception:  # pragma: no cover - exporter init problems shouldn't kill agents
            log.exception("Failed to init OTLP exporter; tracing will be local-only")
    trace.set_tracer_provider(provider)
    TRACER = provider.get_tracer("tinyclaw")
    _provider_set = True


# ---------------------------------------------------------------------------
# Cross-agent propagation
# ---------------------------------------------------------------------------

TRACE_KEY = "traceparent"


def inject_trace_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return message metadata carrying the current span's W3C tracecontext."""
    metadata = dict(metadata or {})
    inject(metadata)
    return metadata


def extract_trace_metadata(metadata: dict[str, Any] | None) -> Any:
    """Rebuild the parent context from incoming A2A message metadata."""
    return extract(dict(metadata or {}))


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


@contextmanager
def span(name: str, parent: Any = None, kind: SpanKind = SpanKind.INTERNAL, **attrs: Any) -> Iterator[Any]:
    """Opinionated sync span helper: records exceptions and sets SUCCESS status."""
    cm = (
        TRACER.start_as_current_span(name, context=parent, kind=kind, attributes={k: str(v) for k, v in attrs.items()})
        if parent is not None
        else TRACER.start_as_current_span(name, kind=kind, attributes={k: str(v) for k, v in attrs.items()})
    )
    with cm as s:
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


@asynccontextmanager
async def aspan(name: str, parent: Any = None, kind: SpanKind = SpanKind.INTERNAL, **attrs: Any) -> AsyncIterator[Any]:
    """Async variant of :func:`span` for coroutine bodies."""
    cm = (
        TRACER.start_as_current_span(name, context=parent, kind=kind, attributes={k: str(v) for k, v in attrs.items()})
        if parent is not None
        else TRACER.start_as_current_span(name, kind=kind, attributes={k: str(v) for k, v in attrs.items()})
    )
    with cm as s:
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def record_llm_span(provider: str, model: str, prompt: str, completion: str, agent: str = "") -> None:
    """Record an LLM call using GenAI semantic conventions (best-effort, non-fatal)."""
    try:
        with TRACER.start_as_current_span(f"llm.{provider}", kind=SpanKind.CLIENT) as s:
            s.set_attribute("gen_ai.system", provider)
            s.set_attribute("gen_ai.request.model", model)
            s.set_attribute("gen_ai.response.model", model)
            s.set_attribute("gen_ai.prompt", prompt)
            s.set_attribute("gen_ai.completion", completion)
            if agent:
                s.set_attribute("tinyclaw.agent", agent)
    except Exception:  # pragma: no cover
        log.debug("llm span recording failed", exc_info=True)
