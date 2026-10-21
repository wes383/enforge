from __future__ import annotations

import base64
import logging
from io import StringIO
from uuid import uuid4

import httpx

from dr_core.observability.client import LangfuseIngestionClient
from dr_core.observability.redaction import (
    install_log_redaction,
    redact_text,
    sanitize_metadata,
)
from dr_core.observability.tracing import ResearchTracer


class RecordingClient:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []
        self.closed = False

    def send_batch(self, events):
        self.batches.append(list(events))
        return True

    def close(self) -> None:
        self.closed = True


def test_tracer_records_nested_observations_and_usage():
    client = RecordingClient()
    tracer = ResearchTracer(
        run_id=uuid4(),
        project_id=uuid4(),
        environment="test",
        app_version="0.1.0",
        client=client,
    )
    tracer.start(input_metadata={"cases": 1})
    with tracer.span(
        "node.search_sources",
        metadata={"node": "search_sources", "api_key": "tvly-should-not-leak"},
    ) as node:
        with tracer.generation(
            "llm.generate",
            model="test-model",
            metadata={"prompt_chars": 12, "input": "token=secret-value"},
        ) as generation:
            generation.update(
                output={"response_chars": 4},
                usage={"input": 10, "output": 5, "total": 15},
            )
        with tracer.span("search.mock") as failed:
            failed.update(output={"result_count": 2})
    tracer.score("citation_validity_rate", 1.0)
    tracer.finish(status="completed", output={"status": "completed"})
    tracer.flush()

    events = client.batches[0]
    types = [event["type"] for event in events]
    assert types[0] == "trace-create"
    assert "span-create" in types
    assert "generation-create" in types
    assert "generation-update" in types
    assert "score-create" in types
    assert types[-1] == "trace-create"
    assert all(
        event["body"].get("traceId") == tracer.trace_id
        for event in events
        if "traceId" in event["body"]
    )
    generation_event = next(
        event for event in events if event["type"] == "generation-create"
    )
    node_event = next(
        event
        for event in events
        if event["type"] == "span-create"
        and event["body"]["name"] == "node.search_sources"
    )
    assert generation_event["body"]["parentObservationId"] == node_event["body"]["id"]
    generation_update = next(
        event for event in events if event["type"] == "generation-update"
    )
    assert generation_update["body"]["usageDetails"] == {
        "input": 10,
        "output": 5,
        "total": 15,
    }
    serialized = str(events)
    assert "tvly-should-not-leak" not in serialized
    assert "secret-value" not in serialized
    assert "[REDACTED]" in serialized


def test_error_span_is_marked_failed():
    client = RecordingClient()
    tracer = ResearchTracer(
        run_id=uuid4(),
        project_id=uuid4(),
        environment="test",
        app_version="0.1.0",
        client=client,
    )
    tracer.start()
    try:
        with tracer.span("node.fail"):
            raise RuntimeError("search api_key=abc12345 failed")
    except RuntimeError:
        pass
    tracer.finish(status="failed")
    tracer.flush()

    update = next(
        event
        for event in client.batches[0]
        if event["type"] == "span-update"
    )
    assert update["body"]["level"] == "ERROR"
    assert "abc12345" not in update["body"]["statusMessage"]


def test_redaction_and_metadata_sanitization():
    text = (
        "Authorization: Bearer abcdefgh12345678 "
        "api_key=tvly-abcdefgh password=super-secret"
    )
    redacted = redact_text(text)
    assert "abcdefgh12345678" not in redacted
    assert "tvly-abcdefgh" not in redacted
    assert "super-secret" not in redacted
    metadata = sanitize_metadata(
        {
            "api_key": "plain",
            "nested": {"secret_key": "hidden", "count": 2},
            "note": "token=abc",
        }
    )
    assert metadata["api_key"] == "[REDACTED]"
    assert metadata["nested"]["secret_key"] == "[REDACTED]"
    assert metadata["nested"]["count"] == 2
    assert "abc" not in metadata["note"]


def test_log_filter_redacts_handler_output():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"phase9.redaction.{uuid4().hex}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    install_log_redaction(logger)
    logger.info("provider failed with api_key=tvly-very-secret and password=hunter2")
    output = stream.getvalue()
    assert "tvly-very-secret" not in output
    assert "hunter2" not in output
    assert "[REDACTED]" in output


def test_langfuse_client_uses_basic_auth_and_does_not_raise_on_failure():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"successes": [], "errors": []})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = LangfuseIngestionClient(
        host="https://langfuse.test",
        public_key="public-key",
        secret_key="secret-key",
        client=http_client,
    )
    assert client.send_batch([{"id": "event-1", "type": "trace-create"}])
    assert requests[0].url.path == "/api/public/ingestion"
    expected = base64.b64encode(b"public-key:secret-key").decode()
    assert requests[0].headers["authorization"] == f"Basic {expected}"

    def failing(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    failing_client = LangfuseIngestionClient(
        host="https://langfuse.test",
        public_key="public-key",
        secret_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(failing)),
    )
    assert failing_client.send_batch([{"id": "event-2"}]) is False
