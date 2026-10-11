"""Tracing to Azure Application Insights, which the Foundry portal's Tracing page reads.

Agent Framework instruments every workflow step, agent run and model call with
OpenTelemetry (GenAI semantic conventions); the orchestrator adds one span per trigger or
producer decision, and the Auditor one per code check. This adapter only chooses where
those spans go: nowhere (``off``, the default, also for tests and local runs) or Application
Insights through the Azure Monitor OpenTelemetry distro.

- Sign-in: Microsoft Entra ID (``DefaultAzureCredential``). The resource refuses key-based
  ingestion, so the identity needs *Monitoring Metrics Publisher* on it; the connection
  string only says where to send.
- Volume: library auto-instrumentation (HTTP, Azure SDK calls) and log export stay off, so a
  match's ~8,000 feed messages and its ledger writes add no spans. The Log Analytics
  workspace's daily cap bounds the cost.
- ``trace_content`` records prompts and replies on the spans. Pitch Ledger's data is
  synthetic with fictional names, so the demo turns it on; production would leave it off.
"""

from typing import Any, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.core.checks import Check, short_error

INGEST_SCOPE = "https://monitor.azure.com/.default"
AUTO_INSTRUMENTED = ("azure_sdk", "django", "fastapi", "flask", "httpx", "httpx2", "psycopg2",
                     "requests", "urllib", "urllib3")  # fmt: skip
"""The distro's library instrumentations, all turned off (only our spans and the agents')."""


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    tracing: Literal["off", "azure"] = "off"
    trace_content: bool = False  # prompts and replies on the spans (synthetic data only)
    appinsights_connection: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "APPLICATIONINSIGHTS_CONNECTION_STRING", "PITCH_LEDGER_APPINSIGHTS_CONNECTION"
        ),
    )

    def problems(self) -> list[str]:
        if self.tracing == "azure" and not self.appinsights_connection:
            return ["azure tracing needs APPLICATIONINSIGHTS_CONNECTION_STRING"]
        return []

    def describe(self) -> str:
        if self.tracing == "off":
            return "off"
        endpoint = _ingestion_endpoint(self.appinsights_connection or "") or "no endpoint"
        content = "with prompts and replies" if self.trace_content else "timings only"
        return f"azure {endpoint} ({content})"


def _ingestion_endpoint(connection: str) -> str | None:
    parts = dict(p.split("=", 1) for p in connection.split(";") if "=" in p)
    return parts.get("IngestionEndpoint")


_configured = False


def configure_tracing(service: str, settings: TelemetrySettings | None = None) -> bool:
    """Send spans to Application Insights when ``tracing=azure``; True when it did.

    Call once at process start (the CLI does, for ``replay``, ``live`` and ``api``). Spans
    are batched and flushed at exit."""
    global _configured
    settings = settings or TelemetrySettings()
    if settings.tracing == "off":
        return False
    if problems := settings.problems():
        raise ValueError("; ".join(problems))
    if _configured:
        return True
    from agent_framework.observability import (
        create_metric_views,
        create_resource,
        enable_instrumentation,
    )
    from azure.identity import DefaultAzureCredential
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(
        connection_string=settings.appinsights_connection,
        credential=DefaultAzureCredential(),
        resource=create_resource(service_name=service),
        views=create_metric_views(),
        instrumentation_options={name: {"enabled": False} for name in AUTO_INSTRUMENTED},
        disable_logging=True,
        enable_live_metrics=False,
    )
    enable_instrumentation(enable_sensitive_data=settings.trace_content)
    _configured = True
    return True


def check_tracing(settings: TelemetrySettings | None = None, exporter: Any = None) -> list[Check]:
    """Settings, Entra ID sign-in for ingestion, and one test span sent and accepted."""
    settings = settings or TelemetrySettings()
    checks = [Check("PITCH_LEDGER_TRACING", settings.tracing == "azure", settings.describe())]
    if problems := settings.problems():
        checks.append(Check("APPLICATIONINSIGHTS_CONNECTION_STRING", False, problems[0]))
    if settings.tracing != "azure" or problems:
        return checks
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    if exporter is None:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        try:
            credential.get_token(INGEST_SCOPE)
            checks.append(Check("Entra ID sign-in", True, "token issued for Azure Monitor"))
        except Exception as exc:  # wrong tenant, client or secret
            checks.append(Check("Entra ID sign-in", False, short_error(exc)))
            return checks
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporter = AzureMonitorTraceExporter(
            connection_string=settings.appinsights_connection,
            credential=credential,
            disable_offline_storage=True,
        )
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "pitch-ledger-check"}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("pitch_ledger").start_as_current_span("pitch_ledger.check_tracing"):
        pass
    try:
        result = exporter.export(memory.get_finished_spans())
        ok = getattr(result, "name", str(result)) == "SUCCESS"
        detail = "test span accepted" if ok else f"export result {result} (role missing?)"
    except Exception as exc:
        ok, detail = False, short_error(exc)
    checks.append(Check("test span", ok, detail))
    return checks
