"""Optional observability setup for LLM/RAG tracing.

Phoenix tracing is disabled by default. When enabled, this module registers
OpenTelemetry exporters and instruments LlamaIndex so retrieval and LLM spans
can be inspected in Phoenix without changing the RAG workflow code.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from app.config import settings

logger = logging.getLogger(__name__)

_TRACER_NAME = "weiquiz.agentic_rag"


@dataclass(frozen=True)
class ObservabilityStatus:
    enabled: bool
    provider: str
    project_name: str
    endpoint: str
    error: str = ""


def setup_observability() -> ObservabilityStatus:
    """Configure optional Phoenix tracing.

    Returns a status object instead of raising so local development remains
    usable even when Phoenix dependencies or the Phoenix server are absent.
    """

    provider = (settings.observability_provider or "phoenix").strip().lower()
    if not settings.observability_enabled:
        return ObservabilityStatus(
            enabled=False,
            provider=provider,
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_endpoint,
        )

    if provider != "phoenix":
        return ObservabilityStatus(
            enabled=False,
            provider=provider,
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_endpoint,
            error=f"Unsupported observability provider: {provider}",
        )

    try:
        from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
        from phoenix.otel import register

        tracer_provider = register(
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_endpoint,
            auto_instrument=False,
        )
        LlamaIndexInstrumentor().instrument(tracer_provider=tracer_provider)
        logger.info(
            "Phoenix observability enabled: project=%s endpoint=%s",
            settings.phoenix_project_name,
            settings.phoenix_endpoint,
        )
        return ObservabilityStatus(
            enabled=True,
            provider=provider,
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_endpoint,
        )
    except Exception as exc:
        logger.warning("Phoenix observability setup failed: %s", exc)
        return ObservabilityStatus(
            enabled=False,
            provider=provider,
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_endpoint,
            error=str(exc),
        )


def _safe_attribute_value(value: Any) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        text = value if not isinstance(value, str) else value[:2000]
        return text
    return str(value)[:2000]


def set_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    if span is None:
        return
    for key, value in attributes.items():
        try:
            span.set_attribute(key, _safe_attribute_value(value))
        except Exception:
            continue


def add_span_event(span: Any, name: str, attributes: dict[str, Any] | None = None) -> None:
    if span is None:
        return
    try:
        span.add_event(name, {k: _safe_attribute_value(v) for k, v in (attributes or {}).items()})
    except Exception:
        return


@contextmanager
def start_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start an optional business span.

    This is intentionally defensive: observability must never break the RAG
    request path, especially during local development.
    """

    if not settings.observability_enabled:
        yield None
        return

    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(_TRACER_NAME)
        with tracer.start_as_current_span(name) as span:
            set_span_attributes(span, attributes)
            yield span
    except Exception:
        yield None
