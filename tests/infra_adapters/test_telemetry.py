"""Tracing adapter: off by default, settings never echo the key, and the ingestion check."""

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from pitch_ledger.infra_adapters.telemetry import (
    TelemetrySettings,
    check_tracing,
    configure_tracing,
)

CONNECTION = ("InstrumentationKey=00000000-0000-0000-0000-000000000000;"
              "IngestionEndpoint=https://eastus-8.in.applicationinsights.azure.com/")  # fmt: skip


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PITCH_LEDGER_TRACING", "PITCH_LEDGER_TRACE_CONTENT",
                 "APPLICATIONINSIGHTS_CONNECTION_STRING"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_tracing_is_off_by_default_and_configures_nothing() -> None:
    settings = TelemetrySettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.tracing == "off" and not settings.trace_content
    assert configure_tracing("pitch-ledger-test", settings) is False
    assert check_tracing(settings)[0].ok is False  # the check says tracing is off


@pytest.mark.usefixtures("clean_env")
def test_settings_name_what_is_missing_and_show_only_the_endpoint() -> None:
    missing = TelemetrySettings(_env_file=None, tracing="azure")  # type: ignore[call-arg]
    assert missing.problems() == ["azure tracing needs APPLICATIONINSIGHTS_CONNECTION_STRING"]
    with pytest.raises(ValueError, match="APPLICATIONINSIGHTS"):
        configure_tracing("pitch-ledger-test", missing)
    ready = TelemetrySettings(_env_file=None, tracing="azure", trace_content=True,  # type: ignore[call-arg]
                              APPLICATIONINSIGHTS_CONNECTION_STRING=CONNECTION)  # fmt: skip
    assert ready.problems() == []
    assert ready.describe() == ("azure https://eastus-8.in.applicationinsights.azure.com/ "
                                "(with prompts and replies)")  # fmt: skip
    assert "InstrumentationKey" not in ready.describe()


class FakeExporter:
    def __init__(self, result: SpanExportResult) -> None:
        self.result, self.spans = result, []

    def export(self, spans: list) -> SpanExportResult:  # type: ignore[type-arg]
        self.spans += spans
        return self.result


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("result", "ok"), [(SpanExportResult.SUCCESS, True),
                                            (SpanExportResult.FAILURE, False)])  # fmt: skip
def test_check_sends_one_test_span(result: SpanExportResult, ok: bool) -> None:
    settings = TelemetrySettings(_env_file=None, tracing="azure",  # type: ignore[call-arg]
                                 APPLICATIONINSIGHTS_CONNECTION_STRING=CONNECTION)  # fmt: skip
    exporter = FakeExporter(result)
    checks = check_tracing(settings, exporter=exporter)
    assert checks[-1].name == "test span" and checks[-1].ok is ok
    assert [s.name for s in exporter.spans] == ["pitch_ledger.check_tracing"]
