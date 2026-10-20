"""OpenTelemetry instrumentation setup for traces and metrics."""

import logging

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ml_mcp import __version__

logger = logging.getLogger(__name__)


# Global metric instruments container
class MetricInstruments:
    def __init__(self, meter: metrics.Meter) -> None:
        self.meter = meter
        self.mcp_requests_total = meter.create_counter(
            name="mcp_requests_total",
            description="Total number of MCP requests handled",
            unit="1",
        )
        self.mcp_request_duration_seconds = meter.create_histogram(
            name="mcp_request_duration_seconds",
            description="Latency distribution of MCP requests",
            unit="s",
        )
        self.mcp_errors_total = meter.create_counter(
            name="mcp_errors_total",
            description="Total number of MCP error responses returned",
            unit="1",
        )
        self.auth_failures_total = meter.create_counter(
            name="auth_failures_total",
            description="Total authentication failures",
            unit="1",
        )
        self.authorization_denials_total = meter.create_counter(
            name="authorization_denials_total",
            description="Total authorization rejections",
            unit="1",
        )
        self.rate_limit_denials_total = meter.create_counter(
            name="rate_limit_denials_total",
            description="Total rate limit throttle events",
            unit="1",
        )
        self.experiments_created_total = meter.create_counter(
            name="experiments_created_total",
            description="Total experiments created",
            unit="1",
        )
        self.experiments_succeeded_total = meter.create_counter(
            name="experiments_succeeded_total",
            description="Total experiments successfully completed",
            unit="1",
        )
        self.experiments_failed_total = meter.create_counter(
            name="experiments_failed_total",
            description="Total experiments that failed during execution",
            unit="1",
        )
        self.experiment_runtime_seconds = meter.create_histogram(
            name="experiment_runtime_seconds",
            description="Execution duration of ML experiment runs",
            unit="s",
        )
        self.artifact_bytes_written = meter.create_counter(
            name="artifact_bytes_written",
            description="Total bytes written to artifact store",
            unit="By",
        )
        self.analysis_runtime_seconds = meter.create_histogram(
            name="analysis_runtime_seconds",
            description="Runtime of structured analysis evaluation",
            unit="s",
        )


_instruments: MetricInstruments | None = None


def init_telemetry(
    service_name: str = "ml-mcp-service",
    enabled: bool = False,
    otlp_endpoint: str = "http://localhost:4317",
) -> MetricInstruments:
    """Initialize OpenTelemetry SDK with resource attributes and OTLP exporters if enabled."""
    global _instruments
    resource = Resource.create({"service.name": service_name})

    if enabled:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            # Traces
            tracer_provider = TracerProvider(resource=resource)
            span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)

            # Metrics
            metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
            reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)
            logger.info("OpenTelemetry initialized with OTLP endpoint: %s", otlp_endpoint)
        except Exception as exc:
            logger.warning(
                "Failed to initialize OTLP exporter, falling back to default providers: %s", exc
            )
            trace.set_tracer_provider(TracerProvider(resource=resource))
            metrics.set_meter_provider(MeterProvider(resource=resource))
    else:
        trace.set_tracer_provider(TracerProvider(resource=resource))
        metrics.set_meter_provider(MeterProvider(resource=resource))

    meter = metrics.get_meter("ml-mcp", __version__)
    _instruments = MetricInstruments(meter)
    return _instruments


def get_tracer(name: str = "ml-mcp") -> trace.Tracer:
    """Return an OpenTelemetry Tracer instance."""
    return trace.get_tracer(name, __version__)


def get_meter(name: str = "ml-mcp") -> metrics.Meter:
    """Return an OpenTelemetry Meter instance."""
    return metrics.get_meter(name, __version__)


def get_instruments() -> MetricInstruments:
    """Return instantiated metric counters and histograms."""
    global _instruments
    if _instruments is None:
        meter = metrics.get_meter("ml-mcp", __version__)
        _instruments = MetricInstruments(meter)
    return _instruments
