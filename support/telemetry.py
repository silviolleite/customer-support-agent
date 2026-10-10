"""Tracing: every span goes over OTLP to the Phoenix process started by run.sh (O-1).

Stage 5 decision: spans record full content (messages, prompts, tool results), so Phoenix
holds customer data; it listens on 127.0.0.1 only."""

import os

# Before the LiteLLM instrumentor imports litellm: config sets LiteLLM's environment.
import support.config  # noqa: F401  # isort: skip

from openinference.instrumentation.litellm import LiteLLMInstrumentor
from openinference.semconv.resource import ResourceAttributes
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

PHOENIX_URL = os.environ.get("PHOENIX_URL", "http://127.0.0.1:6006")
PROJECT_NAME = "customer-support"

# ADK names its spans (SPEC §10) but gives them no OpenInference kind, so Phoenix would show
# them as "unknown". Kinds by span-name prefix:
_ADK_SPAN_KINDS = {
    "invocation": OpenInferenceSpanKindValues.CHAIN.value,  # ADK's wrapper around invoke_agent
    "invoke_agent": OpenInferenceSpanKindValues.AGENT.value,
    "call_llm": OpenInferenceSpanKindValues.LLM.value,
    "execute_tool": OpenInferenceSpanKindValues.TOOL.value,
}


class _AdkSpanKinds(SpanProcessor):
    def on_start(self, span, parent_context=None) -> None:
        kind = _ADK_SPAN_KINDS.get(span.name.split(" ", 1)[0])
        if kind and SpanAttributes.OPENINFERENCE_SPAN_KIND not in (span.attributes or {}):
            span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, kind)


_provider: TracerProvider | None = None


def setup() -> trace.Tracer:
    """Install the global tracer provider. Call before any agent is built, so ADK's spans
    and the LiteLLM calls (our stand-in for O-3's google-genai instrumentor) land in it."""
    global _provider
    if _provider is None:
        _provider = TracerProvider(resource=Resource({ResourceAttributes.PROJECT_NAME: PROJECT_NAME}))
        _provider.add_span_processor(_AdkSpanKinds())
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(f"{PHOENIX_URL}/v1/traces")))
        trace.set_tracer_provider(_provider)
        LiteLLMInstrumentor().instrument(tracer_provider=_provider)
    return trace.get_tracer("support.pipeline")


def trace_url(trace_id: str) -> str:
    """A link that opens one trace in Phoenix (O-4)."""
    return f"{PHOENIX_URL}/redirects/traces/{trace_id}"


def flush() -> None:
    """Send buffered spans now: the CLI calls this before it exits."""
    if _provider is not None:
        _provider.force_flush()
